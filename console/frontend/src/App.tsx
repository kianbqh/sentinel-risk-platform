import { useState } from "react";
import { AuthForm } from "./AuthForm";
import { Board } from "./Queue";
import "./styles.css";

export default function App() {
  const [token, setToken] = useState<string | null>(null);
  const [email, setEmail] = useState<string | null>(null);
  const onAuth = (t: string, e: string) => { setToken(t); setEmail(e); };
  const logout = () => { setToken(null); setEmail(null); };
  return (
    <main>
      <header className="masthead"><div><span className="eyebrow">SENTINEL / RISK OPERATIONS</span><h1>Analyst workspace</h1></div>
      {token && <div className="identity">{email}<button onClick={logout}>Log out</button></div>}</header>
      {token ? <Board token={token} /> : <div className="welcome"><h2>A shared view of risk.</h2><p>Review transaction, message and synthetic login-abuse signals. Assign a case, inspect its evidence, and record a decision.</p><AuthForm onAuth={onAuth} /></div>}
    </main>
  );
}
