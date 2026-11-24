/**
 * Headless media discovery.
 *
 * Many platforms (Next.js / SPA players) don't put media URLs in the server HTML
 * — the player fetches them with JavaScript after the page loads. This renders
 * the target in headless Chromium and records the URLs the player requests, so
 * the scanner has something to test.
 *
 * DISCOVERY ONLY. This never captures media bytes and never reports a leak. It
 * hands a list of candidate URLs back to the scanner, which judges each one with
 * a clean, credential-free fetch. If the page authorized itself (e.g. a share
 * link), that authorization stays inside the browser and is NOT used to pull or
 * prove anything — a URL only counts as a leak if it serves with the token and
 * cookies removed.
 */

const UA = "StemBorer/1.0 (+security-self-test; authorized-scope)";

// URLs worth testing: media file extensions or manifests. Fonts/CSS/JS are ignored.
const MEDIA_URL = /\.(mp4|m4v|mov|webm|m3u8|mpd|ts|m4s|mp3|m4a|jpe?g|png|gif|webp|avif)(\?|#|$)/i;
// Response content-types that indicate media.
const MEDIA_CT = /^(video|audio|image)\/|mpegurl|dash\+xml|octet-stream/i;

export async function discoverMedia(target: string, budgetMs = 22000): Promise<string[]> {
  const found = new Set<string>();
  let browser: any = null;

  const consider = (url?: string, ct?: string | null) => {
    if (!url || !/^https?:\/\//i.test(url)) return;
    if (MEDIA_URL.test(url) || (ct && MEDIA_CT.test(ct))) found.add(url);
  };

  try {
    // Dynamic import so the build doesn't bundle the browser into the route.
    const { chromium } = await import("playwright");
    browser = await chromium.launch({
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
    });
    const ctx = await browser.newContext({ userAgent: UA, ignoreHTTPSErrors: true });
    const page = await ctx.newPage();

    page.on("request", (req: any) => consider(req.url()));
    page.on("response", (res: any) => {
      try {
        consider(res.url(), res.headers()["content-type"]);
      } catch {
        /* ignore */
      }
    });

    await page.goto(target, { waitUntil: "domcontentloaded", timeout: budgetMs }).catch(() => {});
    await page.waitForTimeout(4000); // let the player hydrate and request media

    // Best-effort nudge to start playback so the player fetches the stream.
    try {
      await page.evaluate(() => {
        document.querySelectorAll("video,audio").forEach((m) => {
          try {
            (m as HTMLMediaElement).muted = true;
            void (m as HTMLMediaElement).play();
          } catch {
            /* ignore */
          }
        });
      });
    } catch {
      /* ignore */
    }
    try {
      await page.locator("button:has-text('Play'), [aria-label*='lay'], .play, [class*='play']").first().click({ timeout: 1500 });
    } catch {
      /* ignore */
    }
    await page.waitForTimeout(4000);
  } catch {
    // Browser unavailable or navigation failed — the scanner still runs on the
    // static-HTML media it already has.
  } finally {
    try {
      await browser?.close();
    } catch {
      /* ignore */
    }
  }

  return Array.from(found).slice(0, 80);
}
