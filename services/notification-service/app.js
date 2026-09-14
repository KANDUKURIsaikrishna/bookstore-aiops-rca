import express from "express";
import helmet from "helmet";
import morgan from "morgan";
import { Registry, collectDefaultMetrics, Counter, Histogram } from "prom-client";
import { createLogger } from "./logger.js";

const SERVICE_NAME = "notification-service";
const logger = createLogger(SERVICE_NAME);

const registry = new Registry();
registry.setDefaultLabels({ service: SERVICE_NAME });
collectDefaultMetrics({ register: registry });

const httpRequests = new Counter({
  name: "http_requests_total",
  help: "Total HTTP requests",
  labelNames: ["method", "route", "status", "service"],
  registers: [registry],
});

const httpDuration = new Histogram({
  name: "http_request_duration_seconds",
  help: "HTTP request duration in seconds",
  labelNames: ["method", "route", "status", "service"],
  buckets: [0.01, 0.05, 0.1, 0.5, 1, 2],
  registers: [registry],
});

export function createApp(db) {
  const app = express();
  // Behind nginx-ingress/ALB -- without this, req.ip (and anything keyed on
  // it) sees the proxy's address instead of the real client's.
  app.set("trust proxy", 1);
  app.use(helmet());
  // No cors() here: this service is only ever called server-to-server by
  // api-gateway (enforced at the network layer too, see
  // k8s/services/notification-service/base/network-policy.yaml) -- it has
  // no legitimate browser-facing origin to allow.
  app.use(express.json());
  function jsonMorganFormat(tokens, req, res) {
    return JSON.stringify({
      method: tokens.method(req, res),
      url: tokens.url(req, res),
      status: Number(tokens.status(req, res)),
      responseTimeMs: Number(tokens["response-time"](req, res)),
      requestId: req.headers["x-request-id"],
    });
  }
  app.use(morgan(jsonMorganFormat, { stream: { write: (line) => logger.info("http_request", JSON.parse(line)) } }));

  app.use((req, res, next) => {
    const start = Date.now();
    res.on("finish", () => {
      const route = req.route ? req.route.path : req.path;
      const duration = (Date.now() - start) / 1000;
      httpRequests.labels(req.method, route, String(res.statusCode), SERVICE_NAME).inc();
      httpDuration.labels(req.method, route, String(res.statusCode), SERVICE_NAME).observe(duration);
    });
    next();
  });

  app.get("/metrics", async (_req, res) => {
    res.set("Content-Type", registry.contentType);
    res.end(await registry.metrics());
  });

  app.get("/health", (_req, res) => {
    res.status(200).json({ status: "ok" });
  });

  app.post("/notify", (req, res) => {
    const { order_id, channel } = req.body;
    if (!order_id || !channel) {
      return res.status(400).json({ error: "order_id and channel are required" });
    }

    db.query(
      "INSERT INTO notification_log (order_id, channel, status) VALUES (?, ?, 'sent')",
      [order_id, channel],
      (err, result) => {
        if (err) {
          logger.error("db_error", { error: err.message, stack: err.stack });
          return res.status(500).json({ error: "internal error" });
        }
        return res.status(201).json({ id: result.insertId, order_id, channel, status: "sent" });
      }
    );
  });

  return app;
}

export { logger };
