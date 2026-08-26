# Owner Actions — Social Preview and Profile

This document records manual owner actions that cannot be automated via the GitHub API.

## 1. Repository social preview (Settings → General → Social preview)

- **Committed image:** `docs/assets/social-preview.png` — 1280×640 PNG, <1MiB, solid `#0f172a` background, high contrast, deterministic SHA (rebuild via `node scripts/build-social-preview.mjs`).
- **Pages canonical URL:** `https://ianlyoo.github.io/margin-ta/assets/social-preview.png`
- **Remaining owner step (browser):** Repository → **Settings → General → Social preview → Edit → Upload an image** → upload the committed `docs/assets/social-preview.png`.

### Verification

```bash
# Pages OG should serve the committed image directly
curl -fsSL https://ianlyoo.github.io/margin-ta/ | grep -o 'og:image[^>]*content="[^"]*"'
# Expect: https://ianlyoo.github.io/margin-ta/assets/social-preview.png

curl -fsSL https://ianlyoo.github.io/margin-ta/assets/social-preview.png -o /tmp/social.png
python -c "import struct; b=open('/tmp/social.png','rb').read(); print(struct.unpack('>II', b[16:24]))"
# Expect (1280, 640)
```

## 2. Optional: Profile pin

- Pin the repository to your GitHub profile: Profile → **Customize your pins** → select `ianlyoo/margin-ta`.

## Source reproducibility

- Template: `docs/social-preview.html` (deterministic, 1280×640, approved copy only: name + positioning + measured-not-guaranteed cue).
- Build: `node scripts/build-social-preview.mjs` (sharp SVG→PNG, compressionLevel 9, deterministic).
