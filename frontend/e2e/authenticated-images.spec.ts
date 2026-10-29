import { test, expect, type Page } from "@playwright/test";

const TEST_TOKEN = "browser-test-token";
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

async function mockApi(page: Page) {
  const imageRequests: { url: string; authorization: string | undefined }[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    // Unrelated app startup requests must not clear the probe's token through
    // the global fg:unauthorized handler. Image routes below still require auth.
    await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
  });
  await page.route("**/api/pdf/page/*/image**", async (route) => {
    const authorization = route.request().headers().authorization;
    imageRequests.push({ url: route.request().url(), authorization });
    if (authorization !== `Bearer ${TEST_TOKEN}`) {
      await route.fulfill({ status: 401, contentType: "application/json", body: "{}" });
      return;
    }
    await route.fulfill({ status: 200, contentType: "image/png", body: PNG });
  });
  return imageRequests;
}

async function openProbe(page: Page): Promise<void> {
  await page.goto("/");
  await page.evaluate(async () => {
    await import("/e2e/fixtures/authenticated-image-probe.tsx");
  });
}

async function mountProbe(page: Page, sourceUrl: string): Promise<void> {
  await page.evaluate((url) => {
    (window as any).mountAuthenticatedImage(url);
  }, sourceUrl);
  await page.getByTestId("authenticated-image").scrollIntoViewIfNeeded();
}

async function mountGallery(page: Page, count: number): Promise<void> {
  await page.evaluate((imageCount) => {
    (window as any).mountAuthenticatedImageGallery(imageCount);
  }, count);
}

async function objectUrls(page: Page): Promise<{ created: string[]; revoked: string[] }> {
  return page.evaluate(() => (window as any).__authenticatedImageObjectUrls);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((token) => {
    localStorage.setItem("fg_auth_token", token);
    const state = { created: [] as string[], revoked: [] as string[] };
    (window as any).__authenticatedImageObjectUrls = state;
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (blob) => {
      const url = create(blob);
      state.created.push(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      state.revoked.push(url);
      revoke(url);
    };
  }, TEST_TOKEN);
});

test("authenticated image uses bearer fetch and a query-token-free blob URL", async ({ page }) => {
  const requests = await mockApi(page);
  await openProbe(page);

  await mountProbe(page, "/api/pdf/page/1/image?pdf_id=samples%2Fprivate.pdf");

  const image = page.getByTestId("authenticated-image");
  await expect(image).toHaveAttribute("src", /^blob:/);
  expect(requests).toHaveLength(1);
  expect(requests[0].authorization).toBe(`Bearer ${TEST_TOKEN}`);
  expect(new URL(requests[0].url).searchParams.get("token")).toBeNull();
});

test("authenticated image revokes replaced and unmounted object URLs", async ({ page }) => {
  await mockApi(page);
  await openProbe(page);
  await mountProbe(page, "/api/pdf/page/1/image?pdf_id=samples%2Fprivate.pdf");
  await expect(page.getByTestId("authenticated-image")).toHaveAttribute("src", /^blob:/);
  const firstUrl = (await objectUrls(page)).created[0];

  await mountProbe(page, "/api/pdf/page/2/image?pdf_id=samples%2Fprivate.pdf");
  await expect.poll(async () => (await objectUrls(page)).created.length).toBe(2);
  const secondUrl = (await objectUrls(page)).created[1];
  expect(secondUrl).not.toBe(firstUrl);
  await expect.poll(async () => (await objectUrls(page)).revoked).toContain(firstUrl);

  await page.evaluate(() => (window as any).unmountAuthenticatedImage());
  await expect.poll(async () => (await objectUrls(page)).revoked).toContain(secondUrl);
});

test("authenticated image exposes loading and response errors", async ({ page }) => {
  await mockApi(page);
  let releaseResponse!: () => void;
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  await page.route("**/api/pdf/page/3/image**", async (route) => {
    await responseGate;
    await route.fulfill({ status: 403, contentType: "application/json", body: "{}" });
  });
  await openProbe(page);

  await mountProbe(page, "/api/pdf/page/3/image?pdf_id=samples%2Fprivate.pdf");
  await expect(page.getByRole("status")).toContainText("betöltése");
  await expect(page.getByTestId("authenticated-image")).toHaveCount(1);
  releaseResponse();

  await expect(page.getByRole("alert")).toContainText("403");
  await expect(page.getByTestId("authenticated-image")).toHaveCount(1);
  await expect(page.getByTestId("authenticated-image")).not.toHaveAttribute("src", /^blob:/);
});

test("gallery defers offscreen fetches and loads an image near the viewport", async ({ page }) => {
  const requests = await mockApi(page);
  await openProbe(page);
  await mountGallery(page, 30);

  const images = page.getByTestId("gallery-image");
  await expect(images).toHaveCount(30);
  await expect.poll(() => requests.length).toBeGreaterThan(0);
  expect(requests.length).toBeLessThan(10);
  const initialRequestCount = requests.length;

  const target = images.nth(20);
  await target.scrollIntoViewIfNeeded();
  await expect.poll(() => requests.some((request) => (
    new URL(request.url).pathname === "/api/pdf/page/21/image"
  ))).toBe(true);
  expect(requests.length).toBeGreaterThan(initialRequestCount);
  await expect(target).toHaveAttribute("loading", "lazy");
  await expect(target).toHaveAttribute("src", /^blob:/);
});

for (const cleanup of ["source-change", "unmount"] as const) {
  test(`authenticated image aborts an in-flight request on ${cleanup}`, async ({ page }) => {
    await mockApi(page);
    let markStarted!: () => void;
    const started = new Promise<void>((resolve) => {
      markStarted = resolve;
    });
    let releaseResponse!: () => void;
    const responseGate = new Promise<void>((resolve) => {
      releaseResponse = resolve;
    });
    await page.route("**/api/pdf/page/1/image**", async (route) => {
      markStarted();
      await responseGate;
      await route.fulfill({ status: 200, contentType: "image/png", body: PNG }).catch(() => {});
    });
    await openProbe(page);
    await mountProbe(page, "/api/pdf/page/1/image?pdf_id=samples%2Fprivate.pdf");
    await started;

    try {
      const failedRequest = page.waitForEvent("requestfailed", {
        predicate: (request) => new URL(request.url()).pathname === "/api/pdf/page/1/image",
        timeout: 5000,
      });
      if (cleanup === "source-change") {
        await mountProbe(page, "/api/pdf/page/2/image?pdf_id=samples%2Fprivate.pdf");
      } else {
        await page.evaluate(() => (window as any).unmountAuthenticatedImage());
      }

      const request = await failedRequest;
      expect(request.failure()?.errorText).toContain("ERR_ABORTED");
      if (cleanup === "source-change") {
        await expect(page.getByTestId("authenticated-image")).toHaveAttribute("src", /^blob:/);
        await expect(page.getByRole("alert")).toHaveCount(0);
      }
    } finally {
      releaseResponse();
    }
  });
}

test("authenticated image loads when IntersectionObserver is unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "IntersectionObserver", {
      value: undefined,
      configurable: true,
    });
  });
  await mockApi(page);
  await openProbe(page);

  await mountProbe(page, "/api/pdf/page/1/image?pdf_id=samples%2Fprivate.pdf");

  await expect(page.getByTestId("authenticated-image")).toHaveAttribute("src", /^blob:/);
  await expect(page.getByRole("alert")).toHaveCount(0);
});
