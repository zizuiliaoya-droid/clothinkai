// 报表无权限提示（7e R4）：菜单已按 capabilities 隐藏；直接输入 URL 进来、接口 403 时整页显示，
// 不显示全 0 的统计卡或空表。判定用 isForbiddenError（同仓库发货页），页面的查询 403 不重试。
import { Card, Result, Typography } from "antd";

export function ReportForbidden({ name }: { name: string }) {
  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          {name}
        </Typography.Title>
      }
    >
      <Result
        status="403"
        title={`没有权限查看${name}`}
        subTitle="需要查看请联系管理员开通权限。"
      />
    </Card>
  );
}
