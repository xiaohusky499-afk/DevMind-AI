import { useState } from "react";
import { authApi, setAuth } from "../api/qa";

export default function AuthPage({ onLogin }) {
  const [mode, setMode] = useState("login"); // login / register / sms
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");

  // 登录表单
  const [account, setAccount] = useState("");
  const [password, setPassword] = useState("");

  // 注册表单
  const [username, setUsername] = useState("");
  const [nickname, setNickname] = useState("");
  const [regPhone, setRegPhone] = useState("");
  const [regCode, setRegCode] = useState("");
  const [regPassword, setRegPassword] = useState("");

  // 短信登录表单
  const [smsPhone, setSmsPhone] = useState("");
  const [smsCode, setSmsCode] = useState("");

  // 发送验证码后展示开发环境返回的 debug_code
  const [debugCode, setDebugCode] = useState("");

  async function run(action) {
    setLoading(true);
    setMessage("");
    try {
      const data = await action();
      if (data.access_token) {
        setAuth(data.access_token, data.user);
        onLogin(data.user);
      } else {
        setMessage(JSON.stringify(data));
      }
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  async function sendCode(phone) {
    setLoading(true);
    setMessage("");
    setDebugCode("");
    try {
      const data = await authApi.sendCode(phone);
      setDebugCode(data.debug_code || "");
      setMessage(data.message || "验证码已发送");
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        <h2>DevMind-AI</h2>

        <div className="auth-tabs">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
            账号登录
          </button>
          <button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>
            注册
          </button>
          <button className={mode === "sms" ? "active" : ""} onClick={() => setMode("sms")}>
            短信登录
          </button>
        </div>

        {mode === "login" && (
          <div className="form">
            <input placeholder="用户名 / 手机号" value={account} onChange={(e) => setAccount(e.target.value)} />
            <input placeholder="密码" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button disabled={loading} onClick={() => run(() => authApi.login(account, password))}>
              登录
            </button>
          </div>
        )}

        {mode === "register" && (
          <div className="form">
            <input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} />
            <input placeholder="昵称" value={nickname} onChange={(e) => setNickname(e.target.value)} />
            <input placeholder="密码（至少 6 位）" type="password" value={regPassword} onChange={(e) => setRegPassword(e.target.value)} />
            <div className="row">
              <input placeholder="手机号" value={regPhone} onChange={(e) => setRegPhone(e.target.value)} />
              <button disabled={loading} onClick={() => sendCode(regPhone)}>
                发验证码
              </button>
            </div>
            <input placeholder="短信验证码" value={regCode} onChange={(e) => setRegCode(e.target.value)} />
            {debugCode && <p className="tip">开发环境验证码：{debugCode}</p>}
            <button
              disabled={loading}
              onClick={() =>
                run(() =>
                  authApi.register({
                    username,
                    password: regPassword,
                    nickname,
                    phone: regPhone,
                    sms_code: regCode,
                  })
                )
              }
            >
              注册并登录
            </button>
          </div>
        )}

        {mode === "sms" && (
          <div className="form">
            <div className="row">
              <input placeholder="手机号" value={smsPhone} onChange={(e) => setSmsPhone(e.target.value)} />
              <button disabled={loading} onClick={() => sendCode(smsPhone)}>
                发验证码
              </button>
            </div>
            <input placeholder="短信验证码" value={smsCode} onChange={(e) => setSmsCode(e.target.value)} />
            {debugCode && <p className="tip">开发环境验证码：{debugCode}</p>}
            <button disabled={loading} onClick={() => run(() => authApi.smsLogin(smsPhone, smsCode))}>
              短信登录
            </button>
          </div>
        )}

        {message && <p className="message">{message}</p>}
      </div>
    </div>
  );
}
