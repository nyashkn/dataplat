# 0008. How projects get dataplat

- Status: **proposed; owner to decide**

## Options
1. **Private git dependency, pinned tag** (the template's default):
   `dataplat = { git = "https://github.com/nyashkn/dataplat.git", tag = "v0.1.0" }`. uv.lock pins the
   commit. CI needs a read-only token (commented step in ci.yml). Upgrades are explicit: bump the tag,
   `uv lock --upgrade-package dataplat`.
2. **Vendored copy** inside each project. No token needed, but copies drift. That is the duplication
   this platform exists to remove.
3. **Public package on PyPI.** Simplest installs, but it publishes client-shaped code; the
   conventions are generic and the example data is synthetic, yet that is still the owner's call.

## Recommendation
Option 1. Convention updates travel separately via `copier update`.
