param([string]$Python = 'py')

$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$source = Join-Path $project 'src'
$versionFile = Join-Path $source 'lantool\_version.py'
$tunnelVersionFile = Join-Path $source 'silent_tunnel\_version.py'
$icon = Join-Path $source 'lantool\qt\assets\launcher-icon.ico'
$qml = Join-Path $source 'lantool\qt\qml'
$fonts = Join-Path $source 'lantool\qt\assets\fonts'
$controls = Join-Path $source 'lantool\qt\assets\themes\dark\controls'
$monogram = Join-Path $source 'lantool\qt\assets\no-nonsense-monogram.png'
$main = Join-Path $source 'vla_app\main.py'
$out = Join-Path $project 'dist'
$work = Join-Path $project 'build'
$hooks = Join-Path $project 'hooks'

$versionSource = Get-Content -LiteralPath $versionFile -Raw
$match = [regex]::Match($versionSource, '(?m)^__version__ = "(\d+)\.(\d+)\.(\d+)"')
if (-not $match.Success) { throw 'Cannot read the app version.' }
$major = [int]$match.Groups[1].Value
$minor = [int]$match.Groups[2].Value
$patch = [int]$match.Groups[3].Value
$currentVersion = "$major.$minor.$patch"
$oldManifest = Join-Path $out 'manifest.json'
$previousVersion = ''
if (Test-Path -LiteralPath $oldManifest -PathType Leaf) {
    $old = Get-Content -LiteralPath $oldManifest -Raw | ConvertFrom-Json
    if ($old.version) { $previousVersion = [string]$old.version }
    elseif (Test-Path -LiteralPath (Join-Path $out 'NN VLA.exe') -PathType Leaf) {
        $previousVersion = $currentVersion
    }
}
if ($previousVersion -eq $currentVersion) {
    $patch++
    $nextVersion = "$major.$minor.$patch"
    $updated = [regex]::Replace($versionSource, '(?m)^__version__ = "\d+\.\d+\.\d+"',
                                "__version__ = `"$nextVersion`"", 1)
    Set-Content -LiteralPath $versionFile -Value $updated -Encoding utf8 -NoNewline
    Set-Content -LiteralPath $tunnelVersionFile -Value "__version__ = `"$nextVersion`"`n" -Encoding utf8
} else {
    $nextVersion = $currentVersion
}

$appName = "NN VLA V$nextVersion"
$exeName = "$appName.exe"
$exe = Join-Path $out $exeName
New-Item -ItemType Directory -Force -Path $work | Out-Null
$parts = @($major, $minor, $patch, 0) -join ', '
$winVersionFile = Join-Path $work 'windows-version.txt'
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($parts), prodvers=($parts), mask=0x3f,
                    flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
                    date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'No-Nonsense'),
    StringStruct('FileDescription', 'NN VLA portable tunnel'),
    StringStruct('FileVersion', '$nextVersion'),
    StringStruct('ProductName', 'NN VLA'),
    StringStruct('ProductVersion', '$nextVersion')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -LiteralPath $winVersionFile -Encoding utf8

& $Python -m pip install --require-hashes -r (Join-Path $project 'build-requirements.lock')
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the hash-locked build dependencies.' }

$specTemplate = Get-Content -LiteralPath (Join-Path $project 'portable.spec.in') -Raw
$specPath = Join-Path $work "$appName.spec"
$specText = $specTemplate.Replace('__PROJECT__', $project.Replace('\', '/')).Replace('__APP_NAME__', $appName)
Set-Content -LiteralPath $specPath -Value $specText -Encoding utf8
& $Python -m PyInstaller --noconfirm --clean --distpath $out --workpath $work $specPath
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw 'EXE build failed. Previous dist files were kept.'
}

$smokeData = Join-Path $work "smoke-$nextVersion"
$priorData = $env:NN_VLA_DATA
$priorQt = $env:QT_QPA_PLATFORM
try {
    $env:NN_VLA_DATA = $smokeData
    $env:QT_QPA_PLATFORM = 'offscreen'
    $smoke = Start-Process -FilePath $exe -ArgumentList '--self-test' -PassThru -WindowStyle Hidden
    if (-not $smoke.WaitForExit(30000)) {
        $smoke.Kill()
        throw 'EXE smoke test timed out. Previous dist files were kept.'
    }
    if ($smoke.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $smokeData 'self-test.json') -PathType Leaf)) {
        throw 'EXE smoke test failed. Previous dist files were kept.'
    }
    $smokeResult = Get-Content -LiteralPath (Join-Path $smokeData 'self-test.json') -Raw | ConvertFrom-Json
    if ($smokeResult.window_count -ne 1 -or $smokeResult.width -ne 320 -or
        $smokeResult.height -ne 320 -or -not $smokeResult.frameless -or
        $smokeResult.minimum_height -ne 320 -or $smokeResult.maximum_height -le 320 -or
        $smokeResult.worker_probe -ne 'ok' -or
        @($smokeResult.warnings).Count -ne 0) {
        throw 'EXE smoke test returned unexpected window, worker, or QML results.'
    }
} finally {
    $env:NN_VLA_DATA = $priorData
    $env:QT_QPA_PLATFORM = $priorQt
}

$hash = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
$manifest = [ordered]@{
    product = 'NN VLA portable developer build'
    version = $nextVersion
    built_at_utc = [DateTime]::UtcNow.ToString('o')
    executable = $exeName
    size = (Get-Item -LiteralPath $exe).Length
    sha256 = $hash
    native_vla_bundled = $false
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $oldManifest -Encoding utf8
$oldValidation = Join-Path $out 'validation.json'
if (Test-Path -LiteralPath $oldValidation -PathType Leaf) {
    Remove-Item -LiteralPath $oldValidation
}
Get-ChildItem -LiteralPath $out -File -Filter 'NN VLA*.exe' |
    Where-Object { $_.Name -ne $exeName } |
    ForEach-Object {
        try {
            Remove-Item -LiteralPath $_.FullName -ErrorAction Stop
        } catch {
            Write-Warning "Could not remove $($_.Name) while it is running. Close the old app and remove that EXE."
        }
    }

Write-Host "Built $exe"
Write-Host "Version $nextVersion"
Write-Host "SHA-256 $hash"
