import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  AutoComplete,
  Button,
  Drawer,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { createFieldMapping, resetFieldMapping } from "@/features/import/api";
import type { MappingSpec, MappingTarget } from "@/features/import/types";
import { extractErrorMessage } from "@/services/apiClient";
import {
  JUSHUITAN_COLUMNS,
  groupHint,
  initialMappingValues,
  mappingColumnsToSave,
  mappingProblem,
  targetTags,
} from "./mappingColumns";

interface Props {
  open: boolean;
  source: string;
  spec: MappingSpec | undefined;
  onClose: () => void;
}

const COLUMN_OPTIONS = JUSHUITAN_COLUMNS.map((c) => ({ value: c, label: c }));

const TAG_COLORS: Record<string, string> = {
  必填: "red",
  其一必填: "orange",
  仅新建时写入: "blue",
};

/**
 * 商品资料导入的字段映射（8a-4，设计 §4.7）：每个系统字段选「读哪一列」，类型由系统定。
 * 保存成新版本即生效（之后的导入按它读）；「恢复内置默认」下线生效版本，历史版本保留。
 */
export function FieldMappingDrawer({ open, source, spec, onClose }: Props) {
  const qc = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>({});

  // 每次打开按当前生效映射（或内置默认）重新填一遍
  useEffect(() => {
    if (open && spec) setValues(initialMappingValues(spec));
  }, [open, spec]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["import-mapping-spec", source] });
  };

  const saveMutation = useMutation({
    mutationFn: () =>
      createFieldMapping({
        source,
        columns: mappingColumnsToSave(spec?.targets ?? [], values),
      }),
    onSuccess: (m) => {
      message.success(`已保存为自定义第 ${m.version} 版，之后的导入按它读取`);
      refresh();
      onClose();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const resetMutation = useMutation({
    mutationFn: () => resetFieldMapping(source),
    onSuccess: () => {
      message.success("已恢复内置默认映射");
      refresh();
      onClose();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const hint = useMemo(() => groupHint(spec?.targets ?? []), [spec]);

  const columns: ColumnsType<MappingTarget> = [
    {
      title: "系统字段",
      dataIndex: "label",
      width: 200,
      render: (label: string, t) => (
        <Space size={4} wrap>
          <span>{label}</span>
          {targetTags(t).map((tag) => (
            <Tag key={tag} color={TAG_COLORS[tag]}>
              {tag}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: "读哪一列",
      dataIndex: "field",
      render: (field: string, t) => (
        <AutoComplete
          aria-label={`${t.label} 读哪一列`}
          style={{ width: "100%" }}
          value={values[field] ?? ""}
          options={COLUMN_OPTIONS}
          placeholder={`如 ${t.default_col}（留空 = 不读）`}
          allowClear
          filterOption={(input, option) =>
            String(option?.value ?? "").includes(input.trim())
          }
          onChange={(v: string) => setValues((prev) => ({ ...prev, [field]: v ?? "" }))}
        />
      ),
    },
  ];

  function onSave() {
    const problem = mappingProblem(spec?.targets ?? [], values);
    if (problem) {
      message.warning(problem);
      return;
    }
    saveMutation.mutate();
  }

  const active = spec?.active ?? null;

  return (
    <Drawer
      title="商品资料导入 · 字段映射"
      open={open}
      onClose={onClose}
      width={640}
      destroyOnHidden
      extra={
        <Space>
          <Popconfirm
            title="恢复内置默认映射？"
            description="当前自定义版本下线（历史版本保留），之后的导入按内置默认读取。"
            okText="恢复"
            cancelText="取消"
            disabled={!active}
            onConfirm={() => resetMutation.mutate()}
          >
            <Button disabled={!active} loading={resetMutation.isPending}>
              恢复内置默认
            </Button>
          </Popconfirm>
          <Button type="primary" loading={saveMutation.isPending} onClick={onSave}>
            保存为新版本
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message={
            active
              ? `当前：自定义第 ${active.version} 版${
                  active.created_by_name ? `（${active.created_by_name} 保存）` : ""
                }`
              : "当前：内置默认（对齐聚水潭商品资料导出）"
          }
          description={
            <Typography.Text type="secondary">
              只需为系统字段选「读哪一列」，类型由系统定；文件里的其余列忽略，不用删列。
              {hint ? ` 其一必填：${hint}。` : ""}
              「仅新建时写入」的字段只在新建款式 / 单品商品时写入，已有的不比较、不覆盖。
            </Typography.Text>
          }
        />
        <Table
          rowKey="field"
          size="small"
          pagination={false}
          columns={columns}
          dataSource={spec?.targets ?? []}
        />
      </Space>
    </Drawer>
  );
}
