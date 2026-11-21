import { describe, it, expect } from "vitest";
import request from "supertest";
import { createMetricsApp } from "../app.js";

// The metrics listener runs on its own port (METRICS_PORT, default 9464) so
// NetworkPolicy can open it to the EKS API-server proxy that Prometheus
// scrapes through, without opening the app port itself. It must serve
// /metrics and nothing else.
describe("createMetricsApp", () => {
  const app = createMetricsApp();

  it("serves service-labeled prometheus metrics", async () => {
    const res = await request(app).get("/metrics");
    expect(res.status).toBe(200);
    expect(res.text).toContain('service="catalog-service"');
  });

  it("does not expose any application route", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(404);
  });
});
