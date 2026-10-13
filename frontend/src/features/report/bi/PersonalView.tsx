// BI PR 个人版（7e R6，设计 2.6 / Q16）：只有 `report.bi:read_own` 的人看到这一版。
// 读 /bi/personal 的白名单（本人计数与进度），不含任何金额；页面上不出现「¥」。
import { useRef } from "react";
import { Alert, Button, Card, Col, Row, Space, Typography } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { getBiPersonal } from "../api";
import type { TimePreset } from "../types";
import { biImageFilename, useBiImageExport } from "./exportBiImage";
import { KpiCard, MUTED } from "./StoreOverviewTab";

const percent = (value: string | null) =>
  value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;

const count = (n: number) => `${n} 篇`;

interface Props {
  preset: TimePreset;
  dateFrom: string | undefined;
  dateTo: string | undefined;
  /** 区间齐了、筛选记忆回填完才查。 */
  enabled: boolean;
}

export function PersonalView({ preset, dateFrom, dateTo, enabled }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["bi-personal", preset, dateFrom, dateTo],
    enabled,
    queryFn: () => getBiPersonal({ preset, date_from: dateFrom, date_to: dateTo }),
  });
  const bodyRef = useRef<HTMLDivElement>(null);
  const { exporting, run: exportImage } = useBiImageExport();

  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="个人数据加载失败"
        description="请稍后重试，或检查当前账号的 BI 看板权限。"
      />
    );
  }

  const cards = [
    { title: "目标篇数", value: data ? count(data.target_count) : "—" },
    {
      title: "约稿量",
      value: data ? count(data.quote_count) : "—",
      // 目标为 0 时进度为 null：没有排目标，不是 0%
      description: data
        ? data.target_count > 0
          ? `约稿进度 ${percent(data.quote_progress)}`
          : "约稿进度：未设目标"
        : undefined,
    },
    {
      title: "发布量",
      value: data ? count(data.publish_count) : "—",
      description: data ? `发布进度 ${percent(data.publish_progress)}` : undefined,
    },
    { title: "未发布", value: data ? count(data.unpublished_count) : "—" },
    { title: "已取消", value: data ? count(data.cancel_count) : "—" },
    { title: "超时未发", value: data ? count(data.overdue_count) : "—" },
  ];

  return (
    <Card
      title="我的推广进度"
      loading={isLoading}
      styles={{ body: { background: "#f8fafc" } }}
      extra={
        <Button
          icon={<DownloadOutlined />}
          loading={exporting}
          disabled={!data}
          onClick={() =>
            data &&
            exportImage(bodyRef.current, biImageFilename("personal", data.date_from, data.date_to))
          }
        >
          导出图片
        </Button>
      }
    >
      {/* 导出图片截这一块（本人计数，不含金额） */}
      <div ref={bodyRef}>
        {/* 姓名放正文、不放标题栏：375 宽时标题不被挤成「我的…」（评审 r1 L2） */}
        {data ? (
          <Space wrap size={[12, 4]} style={{ marginBottom: 12 }}>
            <Typography.Text strong>{data.pr_name}</Typography.Text>
            <Typography.Text style={{ color: MUTED }}>
              {data.date_from} 至 {data.date_to} · 只统计本人的推广单
            </Typography.Text>
          </Space>
        ) : null}
        <Row gutter={[12, 12]}>
          {cards.map((c) => (
            <Col key={c.title} xs={24} sm={12} lg={8}>
              <KpiCard {...c} />
            </Col>
          ))}
        </Row>
      </div>
    </Card>
  );
}
