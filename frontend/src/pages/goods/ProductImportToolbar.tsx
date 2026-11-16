import { useCallback, useMemo, useState } from "react";
import { Button, Space, Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { getImportAccess, getImportConflictSummary, getImportMappingSpec } from "@/features/import/api";
import { ImportUploadButton } from "@/components/ImportUploadButton";
import { ImportResultModal } from "@/components/ImportResultModal/ImportResultModal";
import { FieldMappingDrawer } from "@/pages/cost/FieldMappingDrawer";
import { templateColumnsFromSpec } from "@/pages/cost/mappingColumns";
import { useReadOnlySupport } from "@/stores/supportStore";

const SOURCE = "manual_style_sku";
const REFRESH_KEYS = [["goods"], ["styles"], ["skus"], ["cost-table"], ["goods-brand-options"], ["import-conflicts"]];

/** 商品与款式共用既有商品资料导入，权限仍按来源判断。 */
export function ProductImportToolbar() {
  const readOnly = useReadOnlySupport();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [batchId, setBatchId] = useState<string | null>(null);
  const [mappingOpen, setMappingOpen] = useState(false);
  const { data: access } = useQuery({ queryKey: ["import-access"], queryFn: getImportAccess });
  const sourceAccess = access?.find((item) => item.source === SOURCE);
  const canUpload = !!sourceAccess?.can_upload && !readOnly;
  const canMap = !!sourceAccess?.can_map && !readOnly;
  const canView = !!sourceAccess?.can_view;
  const { data: spec } = useQuery({
    queryKey: ["import-mapping-spec", SOURCE],
    queryFn: () => getImportMappingSpec(SOURCE),
    enabled: canUpload || canMap,
  });
  const columns = useMemo(() => templateColumnsFromSpec(spec), [spec]);
  const { data: conflicts } = useQuery({
    queryKey: ["import-conflicts", "summary", SOURCE],
    queryFn: () => getImportConflictSummary(SOURCE),
    enabled: canView,
  });
  const refresh = useCallback(() => {
    for (const key of REFRESH_KEYS) void qc.invalidateQueries({ queryKey: key });
  }, [qc]);

  if (!canUpload && !canMap && !(canView && conflicts?.pending)) return null;
  return (
    <div style={{ marginBottom: 16 }}>
      <Space wrap>
        {canUpload && <ImportUploadButton
          source={SOURCE}
          label="导入商品资料"
          invalidateKeys={REFRESH_KEYS}
          templateColumns={columns}
          columnsNote="每行一个颜色尺码规格；按当前映射读取下列表头，其他列忽略。"
          onUploaded={setBatchId}
          tooLargeHint="含内嵌图片的聚水潭导出请分批导出"
        />}
        {canMap && <Button disabled={!spec} onClick={() => setMappingOpen(true)}>字段映射</Button>}
        {canView && (conflicts?.pending ?? 0) > 0 && <Button onClick={() => navigate(`/imports?tab=conflicts&source=${SOURCE}`)}>
          待处理冲突 {conflicts?.pending}
        </Button>}
      </Space>
      {canUpload && <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
        导入款式、颜色尺码、成本及单品资料；套装成员在「商品 / 套装」中配置。
      </Typography.Paragraph>}
      <ImportResultModal open={batchId !== null} batchId={batchId} onComplete={refresh} onClose={() => { setBatchId(null); refresh(); }} />
      {canMap && <FieldMappingDrawer open={mappingOpen} source={SOURCE} spec={spec} onClose={() => setMappingOpen(false)} />}
    </div>
  );
}
