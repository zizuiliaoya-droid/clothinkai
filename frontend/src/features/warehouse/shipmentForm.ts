// 仓库页的纯规则（流程线设计 8.5、3.3 S5 / S6）：导出文件名、回填按钮文案、回填弹窗初值与 body、
// 发货时间校验、导出失败的提示。纯函数，vitest 测。

import type { Dayjs } from "dayjs";
import dayjs from "dayjs";
import type { WarehouseShipmentRow, WarehouseWaybillRequest } from "./types";

/** 下载文件名 `待打单_YYYYMMDD_HHmm.xlsx`（后端的 Content-Disposition 是 shipments.xlsx，不用它）。 */
export function exportFilename(now: Dayjs): string {
  return `待打单_${now.format("YYYYMMDD_HHmm")}.xlsx`;
}

/** 列表接口 403（没有 promotion_ship:fill，如直接输入 URL 进来的 PR / 主管 / 运营）：页面显示无权限，不重试。 */
export function isForbiddenError(err: unknown): boolean {
  return (err as { response?: { status?: unknown } } | null | undefined)?.response?.status === 403;
}

/** 行操作文案：待打单「回填」，已发货「改单号」。 */
export function fillActionLabel(status: WarehouseShipmentRow["ship_status"]): string {
  return status === "已发货" ? "改单号" : "回填";
}

export interface WaybillFormValues {
  courier: string | undefined;
  waybill: string;
  shipped_at: Dayjs | null;
}

/** 回填弹窗初值：已发货带出原快递信息；待打单发货时间默认现在。 */
export function fillInitialValues(row: WarehouseShipmentRow, now: Dayjs): WaybillFormValues {
  return {
    courier: row.ship_courier ?? undefined,
    waybill: row.ship_waybill ?? "",
    shipped_at: row.shipped_at ? dayjs(row.shipped_at) : now,
  };
}

/**
 * 回填 body。待打单没动过发货时间就不传，交给服务器的「现在」（本机时钟偏快时传本机的现在会被 422）；
 * 已发货改单号一律传（不传服务器会把发货时间改成现在）。
 */
export function waybillPayload(
  values: { courier: string; waybill: string; shipped_at: Dayjs | null },
  opts: { status: WarehouseShipmentRow["ship_status"]; shippedAtTouched: boolean }
): WarehouseWaybillRequest {
  const body: WarehouseWaybillRequest = { courier: values.courier, waybill: values.waybill.trim() };
  if (values.shipped_at && (opts.status === "已发货" || opts.shippedAtTouched)) {
    body.shipped_at = values.shipped_at.toISOString();
  }
  return body;
}

/** 发货时间校验：必填、不能晚于现在（与后端 SHIPPED_AT_IN_FUTURE 同口径）。 */
export function shippedAtError(value: Dayjs | null | undefined, now: Dayjs): string | null {
  if (!value) return "请选择发货时间";
  if (value.isAfter(now)) return "发货时间不能晚于现在";
  return null;
}

/**
 * 导出失败的提示。导出请求是 responseType=blob，错误体也是 Blob：读出来按接口的 message 显示
 * （超过 5,000 张时是「命中 N 张，超过导出上限 5000，请缩小范围」）。
 */
export async function exportErrorMessage(err: unknown): Promise<string> {
  const data = (err as { response?: { data?: unknown } } | null | undefined)?.response?.data;
  let payload: unknown = data;
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    try {
      payload = JSON.parse(await data.text());
    } catch {
      return "导出失败";
    }
  }
  const msg = (payload as { message?: unknown } | null | undefined)?.message;
  if (typeof msg === "string" && msg) return msg;
  return err instanceof Error && err.message ? err.message : "导出失败";
}
