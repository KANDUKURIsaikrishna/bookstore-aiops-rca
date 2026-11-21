import { describe, it, expect, vi } from "vitest";
import { createLogger } from "../logger.js";

describe("createLogger", () => {
  it("emits structured logs tagged with the service name", () => {
    const logger = createLogger("user-service");
    const writeSpy = vi.spyOn(logger.transports[0], "log");

    logger.info("test message", { foo: "bar" });

    expect(writeSpy).toHaveBeenCalledTimes(1);
    const [info] = writeSpy.mock.calls[0];
    expect(info.service).toBe("user-service");
    expect(info.level).toBe("info");
    expect(info.message).toBe("test message");
    expect(info.foo).toBe("bar");
    expect(info.timestamp).toBeDefined();
  });

  it("defaults to info level when LOG_LEVEL is unset", () => {
    const logger = createLogger("user-service");
    expect(logger.level).toBe("info");
  });
});
