import { useEffect, useState } from "react";
import { faqApi } from "../api/qa";
import { SOURCE_FILTERS } from "../utils/constants";

export default function FaqPage() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [category, setCategory] = useState("");
  const [team, setTeam] = useState("");
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);
  const [message, setMessage] = useState("");
  const pageSize = 10;

  async function load(nextPage = page) {
    setLoading(true);
    setMessage("");
    try {
      const data = await faqApi.list({ page: nextPage, page_size: pageSize, keyword, category, team });
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
      setDetail(await faqApi.detail(id));
    } catch (error) {
      setMessage(error.message);
    }
  }

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="page">
      <div className="toolbar">
        <input placeholder="搜索问题/答案/关键词" value={keyword} onChange={(e) => setKeyword(e.target.value)} />
        <input placeholder="类别，如 常见报错" value={category} onChange={(e) => setCategory(e.target.value)} />
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
            <th>问题</th>
            <th>类别</th>
            <th>团队</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.id}</td>
              <td>{item.question}</td>
              <td>{item.category || "-"}</td>
              <td>{item.team || "-"}</td>
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
            <h3>{detail.question}</h3>
            <p className="answer">{detail.answer}</p>
            <p><b>出处：</b>{detail.doc_source || "-"}</p>
            <p><b>类别：</b>{detail.category || "-"}</p>
            <p><b>团队：</b>{detail.team || "-"}</p>
            <p><b>权限：</b>{detail.security_level}</p>
            <button onClick={() => setDetail(null)}>关闭</button>
          </div>
        </div>
      )}
    </div>
  );
}
