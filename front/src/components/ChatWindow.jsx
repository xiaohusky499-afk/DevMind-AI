import { useState } from "react";
import { faqApi } from "../api/qa";
import SourceCard from "./SourceCard";

export default function ChatWindow() {
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);

  async function send() {
    const text = question.trim();
    if (!text || loading) return;

    const nextMessages = [...messages, { role: "user", text }];
    setMessages(nextMessages);
    setQuestion("");
    setLoading(true);

    try {
      const data = await faqApi.search(text);
      if (data.found) {
        setMessages([...nextMessages, { role: "assistant", text: data.answer, data }]);
      } else {
        setMessages([...nextMessages, { role: "assistant", text: "知识库暂未收录该问题，建议转人工处理。", data }]);
      }
    } catch (error) {
      setMessages([...nextMessages, { role: "assistant", text: error.message, data: null }]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat-page">
      <div className="chat-window">
        {messages.length === 0 && <p className="chat-empty">输入问题开始查询，例如：MySQL 连接失败怎么办？</p>}

        {messages.map((msg, index) => (
          <div key={index} className={`chat-row ${msg.role}`}>
            <div className="bubble">{msg.text}</div>
            {msg.data?.found && <SourceCard data={msg.data} />}
          </div>
        ))}

        <div className="chat-input">
          <input
            placeholder="请输入技术问题"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
          />
          <button onClick={send} disabled={loading}>
            {loading ? "查询中..." : "发送"}
          </button>
        </div>
      </div>
    </div>
  );
}
