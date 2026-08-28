# FastDL setup

This repo now includes a FastDL landing page and a local build script for Source-engine custom content.

## What this builds

- Hosted page: `iosca_hub_github/ioscahub.github.io/fastdl/index.html`
- Hosted asset root: `iosca_hub_github/ioscahub.github.io/fastdl/<game-dir>/...`
- Build script: `scripts/build_fastdl.py`

The FastDL file tree must match your game server's custom content layout exactly. Valve's FastDL docs note that the folder layout and file name casing must match, and that `.bz2` files are recommended for Source clients.

## IOSoccer server toggle

Before FastDL will matter for custom IOSoccer assets, enable them in:

```text
iosoccer\cfg\autoexec.cfg
```

Set:

```cfg
sv_custom_assets 1
```

## Typical GitHub Pages URL

If your GitHub Pages site is:

```text
https://ioscahub.github.io/
```

and your game folder is `iosoccer`, set:

```cfg
sv_downloadurl "https://ioscahub.github.io/fastdl/iosoccer/"
```

Keep the trailing slash.

## Build content into the site

Example for IOSoccer:

```powershell
python scripts/build_fastdl.py `
  --source "D:\Servers\IOSoccer\iosoccer" `
  --site-root "iosca_hub_github\ioscahub.github.io\fastdl" `
  --game-dir "iosoccer" `
  --clean
```

Example with an extra top-level custom folder:

```powershell
python scripts/build_fastdl.py `
  --source "D:\Servers\IOSoccer\iosoccer" `
  --game-dir "iosoccer" `
  --include-dir "custom" `
  --clean
```

What the script does:

- Copies common FastDL folders such as `maps`, `materials`, `models`, `sound`, `resource`, `particles`, `scripts`, `media`, `overviews`, `sprites`, and `download`
- Preserves the relative folder structure under `fastdl/<game-dir>/`
- Writes a `.bz2` copy beside each copied file
- Skips `maps/*.res`, existing `.bz2`, and temp junk such as `.ztmp`

## Build the KITS T8 pack into the correct IOSoccer path

Your local pack is in:

```text
KITS T8\KITS T8
```

Those folders need to end up on the website at:

```text
fastdl\iosoccer\materials\models\player\custom\teamkits\<kit-folder>\
```

Use:

```powershell
python scripts/build_fastdl.py `
  --source "KITS T8\KITS T8" `
  --game-dir "iosoccer" `
  --include-dir "." `
  --mount-subpath "materials/models/player/custom/teamkits"
```

That will copy folders like `t8_vikipers_home_T3MP0` directly into the correct FastDL web path and generate `.bz2` files beside each asset.

## Mirror the server path exactly in IOSCA Hub

If you want the website to mirror your server layout as:

```text
home/iosoccer/materials/models/player/custom/teamkits
home/iosoccer/maps
```

stage the files into the site root itself instead of the `/fastdl/` folder.

Kits:

```powershell
python scripts/build_fastdl.py `
  --source "KITS T8\KITS T8" `
  --site-root "iosca_hub_github\ioscahub.github.io" `
  --game-dir "home" `
  --include-dir "." `
  --mount-subpath "iosoccer/materials/models/player/custom/teamkits"
```

Maps:

```powershell
python scripts/build_fastdl.py `
  --source "maps" `
  --site-root "iosca_hub_github\ioscahub.github.io" `
  --game-dir "home" `
  --include-dir "." `
  --mount-subpath "iosoccer/maps"
```

With that layout, the download URL becomes:

```cfg
sv_downloadurl "https://ioscahub.github.io/home/iosoccer/"
```

The game will then request files like:

```text
https://ioscahub.github.io/home/iosoccer/materials/models/player/custom/teamkits/t8_vikipers_home_T3MP0/outfield.vtf.bz2
https://ioscahub.github.io/home/iosoccer/maps/6v6_miramar.bsp.bz2
```

## One-click local sync

The guide you quoted only works on hosts that provide a built-in FastDL sync button. For this repo, use the local equivalent:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_iosoccer_fastdl.ps1
```

If you want to rebuild the whole `home/` tree first:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\sync_iosoccer_fastdl.ps1 -CleanHome
```

What it does:

- Copies `KITS T8\KITS T8` into `home/iosoccer/materials/models/player/custom/teamkits`
- Copies `maps` into `home/iosoccer/maps`
- Generates `.bz2` files beside each copied asset

After that, commit and push the GitHub Pages repo so the web host is updated too.

## Publish

1. Commit and push the `iosca_hub_github/ioscahub.github.io/fastdl` changes.
2. Wait for GitHub Pages to publish.
3. Open the landing page:

```text
https://ioscahub.github.io/fastdl/
```

4. Open one real asset URL directly, for example:

```text
https://ioscahub.github.io/fastdl/iosoccer/materials/custom/banner.vtf.bz2
```

If that URL does not download in a browser, the game client will not be able to fetch it either.

## Server config

Add this to the server config that runs after your host's default config:

```cfg
sv_custom_assets 1
sv_allowdownload 1
sv_allowupload 1
net_maxfilesize 512
sv_downloadurl "https://ioscahub.github.io/fastdl/iosoccer/"
```

`sv_downloadurl` must stay under 127 characters according to Valve's docs.

## Verification checklist

1. Put a test client on a clean install or remove one known custom file from the client.
2. Join the server.
3. Confirm the download starts from HTTP instead of slow in-game transfer.
4. If a file falls back to slow download, check:

- The path on the web server exactly matches the server file path
- File name casing matches exactly
- The `.bz2` file exists and opens normally in a browser
- The server is pointing at the correct game folder URL with a trailing slash
- The selected kit folder name on the server matches the web folder name exactly, including spaces, parentheses, and case

## Client-side debugging

Valve's `sv_downloadurl` docs recommend these client console commands when something is missing:

```cfg
download_debug 1
developer 1
```

Look for red `Error Downloading` messages in the client console to spot the exact missing path.

## Notes

- For large MP3 files, `.bz2` often saves very little space, but keeping both raw and `.bz2` copies is the safest default.
- `maps/*.res` files are usually server-side manifests rather than client assets, so the build script skips them by default.
- Some of your kit folders contain spaces and parentheses, such as `t8_nankatsu_away 0(ryan)`. Those must match exactly on the web host. If only those kits fail, rename them on both the server and FastDL host to a simpler path with no spaces.
