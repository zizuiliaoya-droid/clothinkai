// 仓库页的纯规则：导出文件名、回填按钮文案、回填 body、发货时间校验、导出失败提示（流程线 8.5）。
import dayjs from "dayjs";
import { describe, expect, it } from "vitest";
import {
  exportErrorMessage,
  exportFilename,
  fillActionLabel,
  fillInitialValues,
  isForbiddenError,
  shippedAtError,
  waybillPayload,
} from "./shipmentForm";
import type { WarehouseShipmentRow } from "./types";

function row(over: Partial<WarehouseShipmentRow>): WarehouseShipmentRow {
  return {
    id: "p1",
    internal_code: "PR-001",
    style_code: "ST-1",
    display_short_name: "上衣",
    goods_title: null,
    items: [],
    legacy_color_spec: null,
    receiver_name: null,
    receiver_phone: null,
    receiver_address: null,
    receiver_updated_after_push: false,
    items_updated_after_push: false,
    ship_status: "待打单",
    ship_pushed_at: null,
    ship_pushed_by_name: null,
    ship_courier: null,
    ship_waybill: null,
    shipped_at: null,
    ui: {},
    ...over,
  };
}

describe("exportFilename", () => {
  it("待打单_YYYYMMDD_HHmm.xlsx，按本地时间补零", () => {
    expect(exportFilename(dayjs("2026-10-05T09:07:30"))).toBe("待打单_20261005_0907.xlsx");
  });
});

describe("fillActionLabel", () => {
  it("待打单 = 回填，已发货 = 改单号", () => {
    expect(fillActionLabel("待打单")).toBe("回填");
    expect(fillActionLabel("已发货")).toBe("改单号");
  });
});

describe("fillInitialValues", () => {
  const now = dayjs("2026-10-10T12:00:00");

  it("待打单：快递与单号空，发货时间默认现在", () => {
    const v = fillInitialValues(row({}), now);
    expect(v.courier).toBeUndefined();
    expect(v.waybill).toBe("");
    expect(v.shipped_at?.valueOf()).toBe(now.valueOf());
  });

  it("已发货：带出原快递、单号与发货时间", () => {
    const v = fillInitialValues(
      row({
        ship_status: "已发货",
        ship_courier: "顺丰",
        ship_waybill: "SF123",
        shipped_at: "2026-10-09T08:30:00+08:00",
      }),
      now
    );
    expect(v.courier).toBe("顺丰");
    expect(v.waybill).toBe("SF123");
    expect(v.shipped_at?.valueOf()).toBe(dayjs("2026-10-09T08:30:00+08:00").valueOf());
  });
});

describe("waybillPayload", () => {
  const values = { courier: "中通", waybill: "  ZT001  ", shipped_at: dayjs("2026-10-09T08:30:00+08:00") };

  it("待打单没动过发货时间：不传 shipped_at（交给服务器的现在，避免本机时钟偏快 422）", () => {
    expect(waybillPayload(values, { status: "待打单", shippedAtTouched: false })).toEqual({
      courier: "中通",
      waybill: "ZT001",
    });
  });

  it("待打单改过发货时间：传带时区的 ISO", () => {
    const body = waybillPayload(values, { status: "待打单", shippedAtTouched: true });
    expect(body.shipped_at).toBe(values.shipped_at.toISOString());
  });

  it("已发货改单号：一律传原发货时间，否则服务器会改成现在", () => {
    const body = waybillPayload(values, { status: "已发货", shippedAtTouched: false });
    expect(body).toEqual({
      courier: "中通",
      waybill: "ZT001",
      shipped_at: values.shipped_at.toISOString(),
    });
  });
});

describe("shippedAtError", () => {
  const now = dayjs("2026-10-10T12:00:00");

  it("空 → 必填", () => {
    expect(shippedAtError(null, now)).toBe("请选择发货时间");
  });

  it("晚于现在 → 不能选未来", () => {
    expect(shippedAtError(now.add(1, "minute"), now)).toBe("发货时间不能晚于现在");
  });

  it("现在或更早 → 通过", () => {
    expect(shippedAtError(now, now)).toBeNull();
    expect(shippedAtError(now.subtract(3, "day"), now)).toBeNull();
  });
});

describe("exportErrorMessage", () => {
  it("blob 响应里的接口提示（超 5,000 张）原样显示", async () => {
    const data = new Blob([
      JSON.stringify({ code: "EXPORT_TOO_MANY_ROWS", message: "命中 5001 张，超过导出上限 5000，请缩小范围" }),
    ]);
    await expect(exportErrorMessage({ response: { status: 422, data } })).resolves.toBe(
      "命中 5001 张，超过导出上限 5000，请缩小范围"
    );
  });

  it("blob 不是 JSON → 通用文案；JSON 错误对象照 message", async () => {
    await expect(exportErrorMessage({ response: { status: 500, data: new Blob(["oops"]) } })).resolves.toBe(
      "导出失败"
    );
    await expect(
      exportErrorMessage({ response: { status: 403, data: { code: "PERMISSION_DENIED", message: "没有权限" } } })
    ).resolves.toBe("没有权限");
  });
});

describe("isForbiddenError", () => {
  it("只有 403 算无权限；500 / 网络错误 / 空值不算", () => {
    expect(isForbiddenError({ response: { status: 403, data: { code: "PERMISSION_DENIED" } } })).toBe(true);
    expect(isForbiddenError({ response: { status: 500 } })).toBe(false);
    expect(isForbiddenError({ response: { status: 401 } })).toBe(false);
    expect(isForbiddenError(new Error("Network Error"))).toBe(false);
    expect(isForbiddenError(null)).toBe(false);
  });
});
