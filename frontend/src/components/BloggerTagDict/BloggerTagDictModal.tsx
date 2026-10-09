// 8b-3 博主标签字典（§7.4）：系统标签只读、自定义标签（主管 / 管理员增删）、导入缺的标签（§6.6）。
// 能不能增删看接口给的 can_manage（= blogger_tag:write），不在前端写死角色；PR 只能看。
import { useState } from "react";
import {
  Alert,
  Button,
  Empty,
  Input,
  List,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import { DeleteOutlined, LockOutlined, PlusOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createBloggerTag,
  deleteBloggerTag,
  listBloggerTags,
  listMissingBloggerTags,
} from "@/features/blogger/api";
import { missingTagRowsText } from "@/features/blogger/display";
import { extractErrorMessage } from "@/services/apiClient";

const TAG_MAX_LEN = 32;

// §6.6 补回步骤（不做「已完成批次整批重跑」）
const RESTORE_STEPS = [
  "主管把要用的标签逐个「加入字典」",
  "再导一次这些博主：同一个文件会被拦下（该文件已导入），按上面的行号只留这些行另存为新文件再导，或从源头重新导出",
  "博主原来没有类目标签的会直接补上；已有别的标签的，「类目标签」会进冲突，到「冲突处理」按字段筛「类目标签」，多选后「用文件覆盖」",
];

interface Props {
  open: boolean;
  onClose: () => void;
  /** 本页刚上传的博主导入批次；不传时接口取最近一个博主导入批次。 */
  batchId?: string;
}

// 博主页表单的类目标签选项也用这个键（字典增删后一起刷新）
const BLOGGER_TAGS_QUERY_KEY = ["blogger-tags"] as const;

export function BloggerTagDictModal({ open, onClose, batchId }: Props) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState("");

  const dictQuery = useQuery({
    queryKey: BLOGGER_TAGS_QUERY_KEY,
    queryFn: listBloggerTags,
    enabled: open,
  });
  const missingQuery = useQuery({
    queryKey: [...BLOGGER_TAGS_QUERY_KEY, "missing", batchId ?? "latest"],
    queryFn: () => listMissingBloggerTags(batchId),
    enabled: open,
  });

  // 前缀 [blogger-tags] 同时刷新字典与「导入缺的标签」（补了字典清单就变短）
  const refresh = () => qc.invalidateQueries({ queryKey: BLOGGER_TAGS_QUERY_KEY });

  const createMutation = useMutation({
    mutationFn: (value: string) => createBloggerTag({ value }),
    onSuccess: (_, value) => {
      message.success(`已加入字典：${value}`);
      setDraft("");
      void refresh();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const deleteMutation = useMutation({
    mutationFn: (tagId: string) => deleteBloggerTag(tagId),
    onSuccess: () => {
      message.success("已从字典删除");
      void refresh();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  const dict = dictQuery.data;
  const canManage = dict?.can_manage ?? false;
  const missing = missingQuery.data;
  const trimmed = draft.trim();

  return (
    <Modal
      title="博主标签字典"
      open={open}
      onCancel={onClose}
      footer={<Button onClick={onClose}>关闭</Button>}
      width={640}
      destroyOnHidden
    >
      {dictQuery.isLoading ? (
        <Spin />
      ) : dictQuery.isError ? (
        <Alert type="error" showIcon message={extractErrorMessage(dictQuery.error)} />
      ) : (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <section aria-labelledby="blogger-tag-system">
            <Typography.Title level={5} id="blogger-tag-system">
              系统标签
            </Typography.Title>
            <Typography.Paragraph style={{ color: "#475569", marginBottom: 8 }}>
              <LockOutlined aria-hidden /> 系统自动计算，不能修改
            </Typography.Paragraph>
            <Space wrap>
              {(dict?.system_tags ?? []).map((t) => (
                <Tag key={t} color="gold" icon={<LockOutlined aria-hidden />} title="系统标签">
                  {t}
                </Tag>
              ))}
            </Space>
          </section>

          <section aria-labelledby="blogger-tag-custom">
            <Typography.Title level={5} id="blogger-tag-custom">
              自定义标签
            </Typography.Title>
            {canManage ? (
              <div style={{ marginBottom: 12 }}>
                <Space.Compact style={{ width: "100%", maxWidth: 360 }}>
                  <Input
                    aria-label="新标签"
                    placeholder={`新标签（最多 ${TAG_MAX_LEN} 字）`}
                    maxLength={TAG_MAX_LEN}
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onPressEnter={() => trimmed && createMutation.mutate(trimmed)}
                  />
                  <Button
                    type="primary"
                    icon={<PlusOutlined />}
                    disabled={!trimmed}
                    loading={createMutation.isPending}
                    onClick={() => createMutation.mutate(trimmed)}
                  >
                    新增
                  </Button>
                </Space.Compact>
              </div>
            ) : (
              <Typography.Paragraph style={{ color: "#475569", marginBottom: 8 }}>
                字典由主管、管理员维护；你可以在博主上选用这些标签
              </Typography.Paragraph>
            )}
            {dict?.items.length ? (
              <Space wrap>
                {dict.items.map((item) => (
                  <Tag key={item.id} color="blue" style={{ paddingInlineEnd: canManage ? 2 : undefined }}>
                    {item.value}
                    {canManage ? (
                      <Popconfirm
                        title={`从字典删除「${item.value}」？`}
                        description="已经打在博主身上的这个标签不受影响"
                        okText="删除"
                        cancelText="取消"
                        onConfirm={() => deleteMutation.mutate(item.id)}
                      >
                        <Button
                          type="text"
                          size="small"
                          danger
                          aria-label={`删除标签 ${item.value}`}
                          icon={<DeleteOutlined />}
                          style={{ height: 20, width: 20, marginInlineStart: 4 }}
                        />
                      </Popconfirm>
                    ) : null}
                  </Tag>
                ))}
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="字典里还没有标签" />
            )}
          </section>

          <section aria-labelledby="blogger-tag-missing">
            <Typography.Title level={5} id="blogger-tag-missing">
              导入缺的标签
            </Typography.Title>
            {missingQuery.isLoading ? (
              <Spin />
            ) : missingQuery.isError ? (
              <Alert type="error" showIcon message={extractErrorMessage(missingQuery.error)} />
            ) : !missing?.batch_id ? (
              <Typography.Paragraph style={{ color: "#475569" }}>
                还没有博主导入批次
              </Typography.Paragraph>
            ) : missing.items.length === 0 ? (
              <Typography.Paragraph style={{ color: "#475569" }}>
                最近这批导入的类目标签都在字典里
              </Typography.Paragraph>
            ) : (
              <>
                <List
                  size="small"
                  bordered
                  dataSource={missing.items}
                  rowKey="tag"
                  renderItem={(item) => (
                    <List.Item
                      actions={
                        canManage
                          ? [
                              <Button
                                key="add"
                                size="small"
                                loading={
                                  createMutation.isPending && createMutation.variables === item.tag
                                }
                                onClick={() => createMutation.mutate(item.tag)}
                              >
                                加入字典
                              </Button>,
                            ]
                          : undefined
                      }
                    >
                      <List.Item.Meta
                        title={
                          <span>
                            {item.tag} <Typography.Text type="secondary">×{item.count}</Typography.Text>
                          </span>
                        }
                        description={
                          <span style={{ color: "#475569" }}>
                            {missingTagRowsText(item.rows, item.count)}
                          </span>
                        }
                      />
                    </List.Item>
                  )}
                />
                <div style={{ color: "#475569", marginTop: 12 }}>
                  补回步骤：
                  <ol style={{ paddingInlineStart: 20, margin: "4px 0 0" }}>
                    {RESTORE_STEPS.map((s) => (
                      <li key={s}>{s}</li>
                    ))}
                  </ol>
                </div>
              </>
            )}
          </section>
        </Space>
      )}
    </Modal>
  );
}
