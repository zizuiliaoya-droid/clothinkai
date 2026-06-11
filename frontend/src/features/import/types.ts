// U06a 统一导入框架 feature 类型定义。

export type ImportBatchStatus =
  | "processing"
  | "completed"
  | "partial"
  | "failed";

export type ImportJobStatus = "success" | "failed";

export interface ImportBatch {
  id: string;
  source: string;
  file_hash: string;
  original_filename: string;
  mapping_version: number | null;
  status: ImportBatchStatus;
  total_rows: number;
  imported: number;
  failed: number;
  retry_count: number;
  error_summary: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
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
