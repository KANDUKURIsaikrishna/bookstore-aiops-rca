import dotenv from "dotenv";
import { createApp, createMetricsApp, logger } from "./app.js";

dotenv.config();

const targets = {
  catalog: process.env.CATALOG_SERVICE_URL || "http://catalog-service.catalog.svc.cluster.local",
  user: process.env.USER_SERVICE_URL || "http://user-service.user.svc.cluster.local",
  order: process.env.ORDER_SERVICE_URL || "http://order-service.order.svc.cluster.local",
};

const app = createApp(process.env.JWT_SECRET, targets);
const APP_PORT = process.env.APP_PORT || 3000;
app.listen(APP_PORT, () => {
  logger.info("startup", { message: `api-gateway listening on port ${APP_PORT}.` });
});

const METRICS_PORT = process.env.METRICS_PORT || 9464;
createMetricsApp().listen(METRICS_PORT, () => {
  logger.info("startup", { message: `api-gateway metrics listening on port ${METRICS_PORT}.` });
});
