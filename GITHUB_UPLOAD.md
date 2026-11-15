# GitHub upload package

The `github-upload/` directory contains two prepared archives for version **1.0.11**:

| Archive | Use |
| --- | --- |
| `NN-VLA-v1.0.11-source.zip` | Extract into a new GitHub repository. It contains source, assets, build files, tests, licences, and documentation. |
| `NN-VLA-v1.0.11-preview-windows-x64.zip` | Attach to a GitHub **pre-release**. It contains the portable EXE, manifest, validation record, release notes, and licence notices. |

The source archive excludes `build/`, `dist/`, runtime identities and options, local smoke data, caches, the obsolete root `NN VLA.spec`, and the upload archives themselves. The `.gitignore` also excludes these if you work from this directory directly. Review the file list before a public push.

To publish source from the extracted source archive using Git, create an empty GitHub repository first, then run from the extracted directory:

```powershell
git init
git add .
git commit -m "Initial NN VLA source"
git branch -M main
git remote add origin https://github.com/OWNER/REPOSITORY.git
git push -u origin main
```

Replace `OWNER/REPOSITORY` with the URL of the repository you created.

Use the preview ZIP as a release asset rather than committing the EXE to source history. The EXE is unsigned and **does not bundle the native VLA service or driver**. Fresh PCs cannot set up tunnelling with it yet; two-PC operation and automatic VLA removal remain unverified or disabled. The local Defender scan in `validation.json` is specific to this file and PC.

Archive hashes are in `github-upload/SHA256SUMS.txt`. Licence and provenance references are in `THIRD_PARTY_NOTICES.md` and `SOURCE-PROVENANCE.md`.
