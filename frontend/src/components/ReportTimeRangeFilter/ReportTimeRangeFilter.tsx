import { DatePicker, Select, Space } from "antd";
import type { Dayjs } from "dayjs";
import type { TimePreset } from "@/features/report/types";

export type ReportDateRange = [Dayjs, Dayjs] | null;

export const REPORT_TIME_PRESET_OPTIONS: Array<{ label: string; value: TimePreset }> = [
  { label: "近7天", value: "last_7d" },
  { label: "近30天", value: "last_30d" },
  { label: "本月", value: "this_month" },
  { label: "上月", value: "last_month" },
  { label: "自定义", value: "custom" },
];

export function useReportTimeRange(preset: TimePreset, range: ReportDateRange) {
  const isCustom = preset === "custom";
  const dateFrom = isCustom && range ? range[0].format("YYYY-MM-DD") : undefined;
  const dateTo = isCustom && range ? range[1].format("YYYY-MM-DD") : undefined;
  return { isCustom, dateFrom, dateTo, enabled: !isCustom || (!!dateFrom && !!dateTo) };
}

interface Props {
  preset: TimePreset;
  onPresetChange: (value: TimePreset) => void;
  range: ReportDateRange;
  onRangeChange: (value: ReportDateRange) => void;
}

export function ReportTimeRangeFilter({ preset, onPresetChange, range, onRangeChange }: Props) {
  const { isCustom } = useReportTimeRange(preset, range);
  return (
    <Space wrap size={8}>
      <span>时间范围：</span>
      <Select<TimePreset>
        aria-label="时间范围"
        value={preset}
        style={{ width: 140 }}
        options={REPORT_TIME_PRESET_OPTIONS}
        onChange={onPresetChange}
      />
      {isCustom ? (
        <DatePicker.RangePicker
          aria-label="自定义日期范围"
          value={range}
          placeholder={["开始日期", "结束日期"]}
          onChange={(value) => onRangeChange(value as ReportDateRange)}
          allowClear
        />
      ) : null}
    </Space>
  );
}
