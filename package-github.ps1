param([string]$OutputDirectory = (Join-Path $PSScriptRoot 'github-upload'))

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $PSScriptRoot).Path.TrimEnd('\')
$dist = Join-Path $root 'dist'
$manifest = Get-Content -LiteralPath (Join-Path $dist 'manifest.json') -Raw | ConvertFrom-Json
$validation = Get-Content -LiteralPath (Join-Path $dist 'validation.json') -Raw | ConvertFrom-Json
$version = [string]$manifest.version
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid build version in dist/manifest.json.' }
$exe = Join-Path $dist ([string]$manifest.executable)
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'The current EXE is missing.' }
$hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $manifest.sha256 -or $hash -ne $validation.exe_sha256 -or
    $version -ne $validation.version) {
    throw 'The EXE, manifest, and validation record do not match.'
}

$rootFiles = @(
    '.gitignore', '.gitattributes', 'README.md', 'LICENSE',
    'SOURCE-PROVENANCE.md', 'THIRD_PARTY_NOTICES.md',
    'GITHUB_UPLOAD.md', 'RELEASE_NOTES.md', 'requirements.txt',
    'build-requirements.lock', 'build.ps1', 'portable.spec.in',
    'package-github.ps1'
)
$sourceFiles = [System.Collections.Generic.List[string]]::new()
foreach ($name in $rootFiles) {
    $path = Join-Path $root $name
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing source file: $name" }
    $sourceFiles.Add($path)
}
$allowedExtensions = @('.py', '.qml', '.svg', '.png', '.ico', '.ttf', '.txt', '.md')
foreach ($directory in @('src', 'hooks', 'tests', 'licenses')) {
    $path = Join-Path $root $directory
    foreach ($item in (Get-ChildItem -LiteralPath $path -File -Recurse -Force)) {
        if ($item.FullName -match '([\\/])__pycache__([\\/])' -or $item.Extension -in @('.pyc', '.pyo')) { continue }
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Unexpected link: $($item.FullName)" }
        $allowed = $item.Extension.ToLowerInvariant() -in $allowedExtensions -or
                   $item.Name -eq 'qmldir' -or
                   ($directory -eq 'licenses' -and $item.Name -match '^(LICENSE|COPYING)')
        if (-not $allowed) { throw "Review unexpected source file before packaging: $($item.FullName)" }
        $sourceFiles.Add($item.FullName)
    }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$output = (Resolve-Path -LiteralPath $OutputDirectory).Path.TrimEnd('\')
$sourceZip = Join-Path $output "NN-VLA-v$version-source.zip"
$previewZip = Join-Path $output "NN-VLA-v$version-preview-windows-x64.zip"
$sourceTemp = Join-Path $output "NN-VLA-v$version-source.tmp.zip"
$previewTemp = Join-Path $output "NN-VLA-v$version-preview.tmp.zip"

Add-Type -AssemblyName System.IO.Compression
function Add-ZipEntry($archive, [string]$path, [string]$entryName) {
    $entry = $archive.CreateEntry($entryName, [IO.Compression.CompressionLevel]::Optimal)
    $inputStream = [IO.File]::OpenRead($path)
    $entryStream = $entry.Open()
    try { $inputStream.CopyTo($entryStream) }
    finally { $entryStream.Dispose(); $inputStream.Dispose() }
}
function Write-Zip([string]$path, [object[]]$files, [string]$prefix, [bool]$stripDist = $false) {
    $stream = [IO.File]::Open($path, [IO.FileMode]::Create, [IO.FileAccess]::Write)
    $archive = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($file in $files) {
            $fullPath = [IO.Path]::GetFullPath([string]$file)
            $relative = [IO.Path]::GetRelativePath($root, $fullPath).Replace('\', '/')
            if ($relative.StartsWith('../') -or $relative -eq '..') { throw "File outside project: $fullPath" }
            if ($stripDist -and $relative.StartsWith('dist/')) { $relative = $relative.Substring(5) }
            Add-ZipEntry $archive $fullPath "$prefix/$relative"
        }
    } finally { $archive.Dispose(); $stream.Dispose() }
}

$sortedSource = @($sourceFiles | Sort-Object)
Write-Zip $sourceTemp $sortedSource "NN-VLA-v$version"
$releaseFiles = @(
    $exe, (Join-Path $dist 'manifest.json'), (Join-Path $dist 'validation.json'),
    (Join-Path $root 'README.md'), (Join-Path $root 'RELEASE_NOTES.md'),
    (Join-Path $root 'SOURCE-PROVENANCE.md'), (Join-Path $root 'THIRD_PARTY_NOTICES.md'),
    (Join-Path $root 'LICENSE')
) + @(Get-ChildItem -LiteralPath (Join-Path $root 'licenses') -Recurse -File | ForEach-Object FullName) +
    @(Get-ChildItem -LiteralPath (Join-Path $root 'src\lantool\qt\assets\fonts') -File -Filter '*-OFL.txt' | ForEach-Object FullName)
Write-Zip $previewTemp $releaseFiles "NN-VLA-v$version-preview" $true

Move-Item -LiteralPath $sourceTemp -Destination $sourceZip -Force
Move-Item -LiteralPath $previewTemp -Destination $previewZip -Force
Get-ChildItem -LiteralPath $output -File -Filter 'NN-VLA-v*.zip' |
    Where-Object { $_.FullName -notin @($sourceZip, $previewZip) } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName }
$lines = @($sourceZip, $previewZip) | ForEach-Object {
    "$( (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant() )  $(Split-Path -Leaf $_)"
}
$lines | Set-Content -LiteralPath (Join-Path $output 'SHA256SUMS.txt') -Encoding utf8
@($sortedSource | ForEach-Object { [IO.Path]::GetRelativePath($root, $_).Replace('\', '/') }) |
    Set-Content -LiteralPath (Join-Path $output 'source-file-list.txt') -Encoding utf8
Get-Item -LiteralPath $sourceZip, $previewZip | Select-Object Name, Length
