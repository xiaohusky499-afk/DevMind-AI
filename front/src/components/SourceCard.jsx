// FAQ 查询结果引用卡片
export default function SourceCard({ data }) {
  return (
    <div className="source-card">
      <div className="source-line">
        <b>出处：</b>{data.source || "未标注"}
        {data.cached && <span className="badge">缓存命中</span>}
      </div>
      <div className="source-line">
        <span>类别：{data.category || "-"}</span>
        <span>团队：{data.team || "-"}</span>
        <span>权限：{data.security_level || "-"}</span>
      </div>
    </div>
  );
}
