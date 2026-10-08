// U06a 统一导入框架 feature 类型定义。

export type ImportBatchStatus =
  | "processing"
  | "completed"
  | "partial"
  | "failed";

export type ImportJobStatus =
  | "success"
  | "failed"
  | "filled"
  | "skipped"
  | "conflict";

export interface ImportBatch {
  id: string;
  source: string;
  file_hash: string;
  original_filename: string;
  mapping_version: number | null;
  status: ImportBatchStatus;
  total_rows: number;
  /** 商品资料、博主：新增或已覆盖的行；其他来源与原来相同 */
  imported: number;
  failed: number;
  retry_count: number;
  error_summary: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  // 8a-6：仅补空 / 重复已跳过 / 冲突（按行互斥）；带提示的行、补空对象数（不互斥）
  filled: number;
  skipped: number;
  conflicted: number;
  warning_count: number;
  filled_objects: number;
  /** 该批次仍待处理的冲突条数 */
  pending_conflicts: number;
}

/** GET /api/imports/batches/{id}/notes 的一行：提示与补空明细（只有字段名，不含值）。 */
export interface ImportJobNote {
  row_number: number;
  status: ImportJobStatus;
  warnings: string[];
  filled: { object_type: string; object_label: string; fields: string[] }[];
}

export interface ImportJobNotesPage {
  items: ImportJobNote[];
  total: number;
  page: number;
  page_size: number;
}

// 导入冲突（8a-6）

export type ConflictStatus =
  | "pending"
  | "overwritten"
  | "kept"
  | "superseded"
  | "invalid";

export type ConflictObjectType = "style" | "sku" | "goods" | "blogger";

export type ConflictValue =
  | string
  | number
  | boolean
  | null
  | ConflictValue[]
  | { [key: string]: ConflictValue };

export interface ConflictFieldDiff {
  field: string;
  label: string;
  system: ConflictValue;
  file: ConflictValue;
  system_display: string | null;
  file_display: string | null;
  sensitive: boolean;
  /** 没有字段读权限：四个值为空，界面显示「有差异」 */
  masked: boolean;
  /** 从被取代的旧冲突并入的字段 */
  from_batch_id: string | null;
}

export interface ImportConflict {
  id: string;
  source: string;
  batch_id: string | null;
  batch_filename: string | null;
  row_numbers: number[];
  object_type: ConflictObjectType;
  object_id: string;
  object_key: string;
  object_label: string;
  kind: "fields" | "key";
  fields: ConflictFieldDiff[];
  message: string | null;
  status: ConflictStatus;
  created_by: string | null;
  created_at: string;
  resolved_by: string | null;
  resolved_by_name: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  superseded_by: string | null;
  can_resolve: boolean;
  overwritable: boolean;
}

export interface ImportConflictPage {
  items: ImportConflict[];
  total: number;
  page: number;
  page_size: number;
}

export interface ImportConflictFilters {
  page?: number;
  page_size?: number;
  source?: string;
  batch_id?: string;
  status?: ConflictStatus | "all";
  field?: string;
  object_type?: ConflictObjectType;
}

export interface ConflictResolveItem {
  id: string;
  /** 用户看到的系统值（按字段名，原样回传列表里的 system）；用文件覆盖时必填 */
  expected_system_values?: Record<string, ConflictValue>;
}

export interface ConflictResolveRequest {
  decision: "overwrite" | "keep";
  items: ConflictResolveItem[];
  note?: string;
}

export type ConflictResolveOutcome =
  | "resolved"
  | "stale"
  | "gone"
  | "not_pending"
  | "not_overwritable"
  | "invalid_value"
  | "error";

export interface ConflictResolveResult {
  id: string;
  outcome: ConflictResolveOutcome;
  status: string | null;
  current_values: Record<string, ConflictValue> | null;
  masked_fields: string[];
  field: string | null;
  message: string | null;
}

export interface ConflictResolveResponse {
  results: ConflictResolveResult[];
  summary: Record<ConflictResolveOutcome, number>;
}

export interface ImportBatchPage {
  items: ImportBatch[];
  total: number;
  page: number;
  page_size: number;
}

export interface ImportBatchListFilters {
  page?: number;
  page_size?: number;
  source?: string;
  batch_status?: ImportBatchStatus;
  created_at_from?: string;
  created_at_to?: string;
}

export interface ImportUploadResponse {
  batch_id: string;
  status: ImportBatchStatus;
  source: string;
}

/** GET /api/imports/access 的一项：当前用户对某个导入来源的能力（按来源判权，8a-7）。 */
export interface ImportSourceAccess {
  source: string;
  label: string;
  /** 重复规则可切换（商品资料、博主）：导入记录页只对它们显示新计数列与结果 / 冲突入口 */
  configurable: boolean;
  can_view: boolean;
  can_upload: boolean;
  can_map: boolean;
  can_resolve: boolean;
}

// 字段映射版本（EP07-S09）

export interface FieldMappingColumn {
  source_col: string;
  target_field: string;
  required?: boolean;
  type?: "str" | "int" | "decimal" | "date" | "datetime" | "bool";
  transform?: string | null;
}

export interface FieldMappingCreate {
  source: string;
  columns: FieldMappingColumn[];
}

export interface FieldMapping {
  id: string;
  source: string;
  version: number;
  mapping_config: { columns: FieldMappingColumn[] };
  is_active: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

/** 映射目录里的一个目标字段（8a-4）。group 形如「组名:选项」：同组至少满足一个选项（其一必填）。 */
export interface MappingTarget {
  field: string;
  label: string;
  type: string;
  default_col: string;
  aliases: string[];
  required: boolean;
  group: string | null;
  /** 仅新建时写入：已有对象不比较、不覆盖 */
  create_only: boolean;
}

/** 内置默认映射的一列（带别名）。 */
export interface BuiltinMappingColumn {
  source_col: string;
  target_field: string;
  type: string;
  aliases?: string[];
}

/** GET /api/imports/sources/{source}/mapping-spec */
export interface MappingSpec {
  source: string;
  targets: MappingTarget[];
  builtin_columns: BuiltinMappingColumn[];
  /** 当前生效的自定义映射；null = 用内置默认 */
  active: {
    version: number;
    columns: FieldMappingColumn[];
    created_by_name: string | null;
    created_at: string;
  } | null;
}
