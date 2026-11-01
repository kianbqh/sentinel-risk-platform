import { useState } from "react";
import { AuthForm } from "./AuthForm";
import { Board } from "./Board";

export default function App() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("token"));
  const [email, setEmail] = useState<string | null>(() => localStorage.getItem("email"));
  const onAuth = (t: string, e: string) => { localStorage.setItem("token", t); localStorage.setItem("email", e); setToken(t); setEmail(e); };
  const logout = () => { localStorage.clear(); setToken(null); setEmail(null); };
  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 820, margin: "2rem auto" }}>
      <h1>Job Application Tracker</h1>
      {token ? (<><p>{email} <button onClick={logout}>Log out</button></p><Board token={token} /></>) : <AuthForm onAuth={onAuth} />}
    </main>
  );
}
