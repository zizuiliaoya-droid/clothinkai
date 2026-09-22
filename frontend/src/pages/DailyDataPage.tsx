import { useMemo, useState } from "react";
import {
  Button,
  Card,
  DatePicker,
  Input,
  InputNumber,
  Space,
  Table,
  Typography,
} from "antd";
import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType, SorterResult, SortOrder } from "antd/es/table/interface";
import type { Dayjs } from "dayjs";
import { ImportUploadButton } from "@/components/ImportUploadButton";

interface DailyPage<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** 可按区间筛选的数值列。`key` 需与后端 `<key>_min` / `<key>_max` 参数前缀一致。 */
export interface NumericFilterSpec {
  key: string;
  label: string;
  /** 金额类列用两位小数，计数类列用整数。 */
  precision?: number;
}

type QueryParams = Record<string, string | number | undefined>;

interface Props<T extends { id: string; extra?: Record<string, unknown> }> {
  title: string;
  /** typed 固定列。 */
  typedColumns: ColumnsType<T>;
  queryKey: string;
  fetchFn: (params: QueryParams) => Promise<DailyPage<T>>;
  /** 该模块在 final.xlsx 的总列数（用于页头标注）。 */
  totalCols: number;
  /** 导入 adapter source（如 qianniu / wanxiangtai）；传入则显示导入按钮。 */
  importSource?: string;
  /** 导入按钮文案。 */
  importLabel?: string;
  /** 导入支持的表头列（兼容平台导出 Excel）。 */
  importColumns?: string[];
  /** 支持服务端排序的字段名（对应 typed 列的 dataIndex）。 */
  sortableFields?: string[];
  /** 支持区间筛选的数值列。 */
  numericFilters?: NumericFilterSpec[];
  /** 商品ID 搜索框的占位文案（千牛叫「商品ID」，万相台叫「主体ID」）。 */
  idLabel?: string;
}

/**
 * 千牛/站内推广日报通用表格。typed 列固定，其余原始列从 extra JSONB 动态展开，
 * 覆盖 final.xlsx 的完整列（38/72）。数据来源：导入入库。
 *
 * 筛选与排序全部走服务端：这两张表按天累积，客户端过滤只能覆盖当前页。
 */
export function DailyDataPage<
  T extends { id: string; extra?: Record<string, unknown> }
>({
  title,
  typedColumns,
  queryKey,
  fetchFn,
  totalCols,
  importSource,
  importLabel,
  importColumns,
  sortableFields = [],
  numericFilters = [],
  idLabel = "商品ID",
}: Props<T>) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [dateRange, setDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [platformId, setPlatformId] = useState<string>();
  const [ranges, setRanges] = useState<Record<string, [number | null, number | null]>>({});
  const [sortBy, setSortBy] = useState<string>();
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const params = useMemo<QueryParams>(() => {
    const p: QueryParams = { page, page_size: pageSize, sort_dir: sortDir };
    if (sortBy) p.sort_by = sortBy;
    if (platformId) p.platform_id = platformId;
    const [from, to] = dateRange ?? [null, null];
    if (from) p.date_from = from.format("YYYY-MM-DD");
    if (to) p.date_to = to.format("YYYY-MM-DD");
    for (const [key, [lo, hi]] of Object.entries(ranges)) {
      if (lo != null) p[`${key}_min`] = lo;
      if (hi != null) p[`${key}_max`] = hi;
    }
    return p;
  }, [page, pageSize, sortBy, sortDir, platformId, dateRange, ranges]);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: [queryKey, params],
    queryFn: () => fetchFn(params),
  });

  // 动态收集 extra 中出现的所有列名，并排除已作为固定列展示的列（避免重复列 + key 冲突）
  const extraColumns = useMemo<ColumnsType<T>>(() => {
    const typedTitles = new Set(
      typedColumns
        .map((c) => (typeof c.title === "string" ? c.title : ""))
        .filter(Boolean),
    );
    const keys = new Set<string>();
    for (const row of data?.items ?? []) {
      const e = row.extra ?? {};
      Object.keys(e).forEach((k) => {
        if (k && !typedTitles.has(k)) keys.add(k);
      });
    }
    return Array.from(keys).map((k) => ({
      title: k,
      key: `extra_${k}`,
      width: 140,
      render: (_: unknown, r: T) => {
        const v = (r.extra ?? {})[k];
        return v == null || v === "" ? "—" : String(v);
      },
    }));
  }, [data, typedColumns]);

  // 给白名单内的 typed 列挂上服务端排序标记（受控：由 sortBy/sortDir 决定箭头方向）
  const sortableTypedColumns = useMemo<ColumnsType<T>>(() => {
    const allowed = new Set(sortableFields);
    return typedColumns.map((col) => {
      const field = "dataIndex" in col ? String(col.dataIndex) : "";
      if (!field || !allowed.has(field)) return col;
      const order: SortOrder = sortDir === "asc" ? "ascend" : "descend";
      return {
        ...col,
        sorter: true,
        sortOrder: sortBy === field ? order : null,
      };
    });
  }, [typedColumns, sortableFields, sortBy, sortDir]);

  const columns = [...sortableTypedColumns, ...extraColumns];

  function resetFilters() {
    setDateRange(null);
    setPlatformId(undefined);
    setRanges({});
    setSortBy(undefined);
    setSortDir("desc");
    setPage(1);
  }

  function handleTableChange(
    _pagination: unknown,
    _filters: unknown,
    sorter: SorterResult<T> | SorterResult<T>[],
  ) {
    const s = Array.isArray(sorter) ? sorter[0] : sorter;
    if (!s?.order) {
      setSortBy(undefined);
      setSortDir("desc");
    } else {
      setSortBy(String(s.field));
      setSortDir(s.order === "ascend" ? "asc" : "desc");
    }
    setPage(1);
  }

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          {title}
          <Typography.Text type="secondary" style={{ fontSize: 13, marginLeft: 12 }}>
            （final.xlsx {totalCols} 列；当前展示 {columns.length} 列）
          </Typography.Text>
        </Typography.Title>
      }
      extra={
        importSource ? (
          <ImportUploadButton
            source={importSource}
            label={importLabel ?? "导入 Excel"}
            invalidateKeys={[[queryKey]]}
            templateColumns={importColumns}
          />
        ) : null
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <DatePicker.RangePicker
          value={dateRange}
          allowEmpty={[true, true]}
          placeholder={["开始日期", "结束日期"]}
          onChange={(v) => {
            setDateRange(v as [Dayjs | null, Dayjs | null] | null);
            setPage(1);
          }}
        />
        <Input.Search
          placeholder={`搜索${idLabel}`}
          allowClear
          style={{ width: 200 }}
          enterButton={<SearchOutlined />}
          onSearch={(v) => {
            setPlatformId(v || undefined);
            setPage(1);
          }}
        />
        {numericFilters.map((f) => (
          <Space.Compact key={f.key}>
            <InputNumber
              style={{ width: 118 }}
              placeholder={`${f.label}≥`}
              precision={f.precision ?? 0}
              value={ranges[f.key]?.[0] ?? null}
              onChange={(v) => {
                setRanges((r) => ({ ...r, [f.key]: [v, r[f.key]?.[1] ?? null] }));
                setPage(1);
              }}
            />
            <InputNumber
              style={{ width: 118 }}
              placeholder={`${f.label}≤`}
              precision={f.precision ?? 0}
              value={ranges[f.key]?.[1] ?? null}
              onChange={(v) => {
                setRanges((r) => ({ ...r, [f.key]: [r[f.key]?.[0] ?? null, v] }));
                setPage(1);
              }}
            />
          </Space.Compact>
        ))}
        <Button icon={<ReloadOutlined />} onClick={resetFilters}>
          重置
        </Button>
      </Space>

      <Table
        rowKey="id"
        size="small"
        loading={isLoading || isFetching}
        columns={columns}
        dataSource={data?.items ?? []}
        onChange={handleTableChange}
        scroll={{ x: Math.max(1200, columns.length * 140) }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? pageSize,
          total: data?.total ?? 0,
          showSizeChanger: true,
          pageSizeOptions: [20, 50, 100, 200],
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, ps) => {
            setPage(p);
            setPageSize(ps);
          },
        }}
      />
    </Card>
  );
}
