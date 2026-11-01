import { useState, type FormEvent } from "react";
import { gql, Q } from "./api";

export function AuthForm({ onAuth }: { onAuth: (token: string, email: string) => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const d = await gql<Record<string, { token: string; user: { email: string } }>>(Q[mode], { e: email, p: password });
      onAuth(d[mode].token, d[mode].user.email);
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={submit} aria-label="auth">
      <h2>{mode === "login" ? "Log in" : "Create account"}</h2>
      <input aria-label="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
      <input aria-label="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required />
      <button type="submit">{mode === "login" ? "Log in" : "Sign up"}</button>
      <button type="button" onClick={() => setMode(mode === "login" ? "register" : "login")}>
        {mode === "login" ? "Need an account?" : "Have an account?"}
      </button>
      {error && <p role="alert">{error}</p>}
    </form>
  );
}
