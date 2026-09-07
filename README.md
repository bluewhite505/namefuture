# NameFuture — Full SSA Database Edition

This is a static GitHub Pages product. The deployed site builds the **complete published U.S. Social Security Administration national baby-name dataset** (1880 through the latest published year) automatically.

## What changed from the demo

- No 200-name cap.
- Uses every name published in SSA `names.zip` (SSA suppresses name/sex/year combinations with fewer than 5 occurrences for privacy).
- Historical series from 1880 through the latest SSA year.
- Data is split into 26 browser-friendly shards (`a.json` … `z.json`), so users do **not** download the whole database on every visit.
- Current rank, recent trajectory, collision estimate, first/last appearance, and 2030 directional projection are calculated from the real history.

## 1. Add your Stripe Payment Link

Open `config.js` and replace:

```js
STRIPE_PAYMENT_LINK: "https://buy.stripe.com/REPLACE_ME"
```

with your real Stripe Payment Link.

## 2. Upload to GitHub

Create a repository and upload **all files and folders**, including:

- `.github/workflows/deploy-pages.yml`
- `scripts/build_ssa_data.py`
- `index.html`
- `config.js`
- `.nojekyll`

Do not delete the dot folders/files.

## 3. Enable GitHub Pages

Repository → **Settings → Pages → Build and deployment → Source → GitHub Actions**.

Push to `main` (or run the workflow manually from Actions). The included workflow will:

1. download the official SSA `names.zip`;
2. process the entire database;
3. generate `data/meta.json` and the 26 `data/shards/*.json` files;
4. deploy the finished site to GitHub Pages.

The first build may take a few minutes because it processes ~2 million historical name/year records. The visitor-facing site stays fast because only one letter shard is fetched per lookup.

## Data source

Official SSA national baby-name dataset:
`https://www.ssa.gov/oact/babynames/names.zip`

The build script has a public GitHub mirror fallback if SSA is temporarily unavailable during deployment.

## Important product wording

The 2030 result is deliberately labeled a **directional estimate**, not a guaranteed future rank. SSA also suppresses combinations with fewer than 5 occurrences, so “not found” can mean the spelling is below the publication threshold.

## Free-use gate + email capture

The free checker now gives **one successful name result per browser**. After that, any second lookup shows the paid comparison paywall. The limit is stored in both `localStorage` and a cookie. This is intentionally an MVP gate: clearing browser storage/incognito can bypass it. A production-grade one-person limit requires a small backend/serverless store.

Email is requested **after the free result**, not before it. This preserves free-result activation while allowing you to build a parent list. To actually send captured emails somewhere, set `EMAIL_CAPTURE_ENDPOINT` in `config.js` to a webhook/Formspree/automation endpoint accepting JSON `{email,name,source}`.

## Stripe / paid unlock

The current MVP uses a Stripe Payment Link because GitHub Pages is static and must not contain a Stripe secret key. For validation, set Stripe's post-payment redirect to `https://YOUR-USERNAME.github.io/YOUR-REPO/compare-7f9k2.html`. That paid comparison page is included and uses the same full SSA database. A production-grade entitlement system would require a backend or serverless function.


## Test reset

During your own testing, open DevTools → Application → Storage and clear site data, or run `localStorage.clear()` in the console to restore the one free check.
