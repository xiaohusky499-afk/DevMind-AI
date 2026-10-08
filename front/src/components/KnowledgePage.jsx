import { useEffect, useState } from "react";
import { knowledgeApi } from "../api/qa";
import { SOURCE_FILTERS } from "../utils/constants";

export default function KnowledgePage() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [docType, setDocType] = useState("");
  const [team, setTeam] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);
  const [message, setMessage] = useState("");
  const pageSize = 10;

  async function load(nextPage = page) {
    setLoading(true);
    setMessage("");
    try {
      const data = await knowledgeApi.list({ page: nextPage, page_size: pageSize, keyword, doc_type: docType, team });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(nextPage);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(1);
  }, []);

  async function openDetail(id) {
    try {
      setDetail(await knowledgeApi.detail(id));
    } catch (error) {
      setMessage(error.message);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="page">
      <div className="toolbar">
        <input placeholder="搜索来源/文档类型" value={keyword} onChange={(e) => setKeyword(e.target.value)} />
        <input placeholder="文档类型，如 wiki" value={docType} onChange={(e) => setDocType(e.target.value)} />
        <select value={team} onChange={(e) => setTeam(e.target.value)}>
          <option value="">全部团队</option>
          {SOURCE_FILTERS.map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
        <button onClick={() => load(1)} disabled={loading}>查询</button>
      </div>

      {message && <p className="message">{message}</p>}

      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>来源</th>
            <th>类型</th>
            <th>团队</th>
            <th>版本</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.id}</td>
              <td>{item.doc_source}</td>
              <td>{item.doc_type}</td>
              <td>{item.team || "-"}</td>
              <td>{item.version || "-"}</td>
              <td>{item.status}</td>
              <td>
                <button onClick={() => openDetail(item.id)}>详情</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="pagination">
        <button disabled={page <= 1} onClick={() => load(page - 1)}>上一页</button>
        <span>{page} / {totalPages}</span>
        <button disabled={page >= totalPages} onClick={() => load(page + 1)}>下一页</button>
      </div>

      {detail && (
        <div className="modal-mask" onClick={() => setDetail(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>文档详情</h3>
            <p><b>来源：</b>{detail.doc_source}</p>
            <p><b>类型：</b>{detail.doc_type}</p>
            <p><b>团队：</b>{detail.team || "-"}</p>
            <p><b>系统：</b>{detail.system_name || "-"}</p>
            <p><b>版本：</b>{detail.version || "-"}</p>
            <p><b>权限：</b>{detail.security_level}</p>
            <p><b>状态：</b>{detail.status}</p>
            <p><b>Chunk 数：</b>{detail.chunk_count}</p>
            <button onClick={() => setDetail(null)}>关闭</button>
          </div>
        </div>
      )}
    </div>
  );
}
