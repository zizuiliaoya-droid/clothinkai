// 店铺日表行内编辑的格子（7e R4 S8）：四个金额框与「编辑 / 保存 / 取消」。状态在 StoreReportView。
//
// 金额框用 InputNumber stringMode、不设 min / max（失焦时会悄悄夹值），校验交给 storeManual.ts。
// 键盘：Enter 保存（有错或没改动时不动）、Esc 取消；退出编辑后焦点回到这一行的「编辑」按钮。
import type { KeyboardEvent } from "react";
import { Button, InputNumber, Space } from "antd";
import type { StoreReportRow } from "../types";
import type { StoreManualDraft, StoreManualField } from "./storeManual";

export interface StoreManualEditing {
  /** 正在编辑的日期；null = 没在编辑。 */
  day: string | null;
  draft: StoreManualDraft | null;
  errors: Partial<Record<StoreManualField, string>>;
  canSave: boolean;
  saving: boolean;
  /** 校验提示元素的 id，金额框出错时用 aria-describedby 指过去。 */
  hintId: string;
  onStart: (row: StoreReportRow) => void;
  onChange: (field: StoreManualField, value: string | null) => void;
  onSave: () => void;
  onCancel: () => void;
  /** 「编辑」按钮的 ref，退出编辑后把焦点还回去。 */
  editButtonRef: (day: string) => (el: HTMLButtonElement | null) => void;
}

export function ManualAmountInput({
  edit,
  row,
  field,
  label,
  autoFocus = false,
}: {
  edit: StoreManualEditing;
  row: StoreReportRow;
  field: StoreManualField;
  label: string;
  autoFocus?: boolean;
}) {
  const invalid = Boolean(edit.errors[field]);
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (edit.canSave) edit.onSave();
    } else if (e.key === "Escape") {
      e.preventDefault();
      edit.onCancel();
    }
  };
  return (
    <InputNumber<string>
      stringMode
      size="small"
      controls={false}
      autoFocus={autoFocus}
      style={{ width: "100%" }}
      value={edit.draft?.[field] ?? null}
      onChange={(v) => edit.onChange(field, v == null ? null : String(v))}
      onKeyDown={onKeyDown}
      status={invalid ? "error" : undefined}
      aria-label={`${row.date_from} ${label}`}
      aria-invalid={invalid}
      aria-describedby={invalid ? edit.hintId : undefined}
      placeholder="未填"
    />
  );
}

export function ManualActionsCell({ edit, row }: { edit: StoreManualEditing; row: StoreReportRow }) {
  const day = row.date_from;
  if (edit.day === day) {
    return (
      <Space size={4}>
        <Button
          type="primary"
          size="small"
          aria-label={`保存 ${day}`}
          disabled={!edit.canSave}
          loading={edit.saving}
          onClick={edit.onSave}
        >
          保存
        </Button>
        <Button
          size="small"
          aria-label={`取消编辑 ${day}`}
          disabled={edit.saving}
          onClick={edit.onCancel}
        >
          取消
        </Button>
      </Space>
    );
  }
  return (
    <Button
      ref={edit.editButtonRef(day)}
      type="link"
      size="small"
      aria-label={`编辑 ${day}`}
      // 一次只编辑一行
      disabled={edit.day !== null}
      onClick={() => edit.onStart(row)}
    >
      编辑
    </Button>
  );
}
