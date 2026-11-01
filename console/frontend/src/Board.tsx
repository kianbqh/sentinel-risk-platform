import { useCallback, useEffect, useState, type FormEvent } from "react";
import { gql, Q, STATUSES, type Application, type Status } from "./api";

interface ListData { applications: Application[]; stats: { total: number; byStatus: { status: Status; count: number }[] } }

export function Board({ token }: { token: string }) {
  const [data, setData] = useState<ListData | null>(null);
  const [filter, setFilter] = useState<Status | "">("");
  const [search, setSearch] = useState("");
  const [company, setCompany] = useState("");
  const [role, setRole] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await gql<ListData>(Q.list, { s: filter || null, q: search || null }, token));
      setError(null);
    } catch (e) { setError((e as Error).message); }
  }, [filter, search, token]);

  useEffect(() => { load(); }, [load]);

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      await gql(Q.create, { in: { company, role, status: "APPLIED" } }, token);
      setCompany(""); setRole(""); await load();
    } catch (err) { setError((err as Error).message); }
  }

  const act = async (q: string, v: Record<string, unknown>) => {
    try {
      await gql(q, v, token);
      await load();
    } catch (err) { setError((err as Error).message); }
  };

  return (
    <section>
      <p data-testid="stats">
        Total: {data?.stats.total ?? 0}
        {data?.stats.byStatus.map((s) => ` · ${s.status} ${s.count}`)}
      </p>
      <form onSubmit={add} aria-label="new application">
        <input aria-label="company" value={company} onChange={(e) => setCompany(e.target.value)} placeholder="Company" required />
        <input aria-label="role" value={role} onChange={(e) => setRole(e.target.value)} placeholder="Role" required />
        <button type="submit">Add</button>
      </form>
      <div>
        <input aria-label="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search" />
        <select aria-label="filter" value={filter} onChange={(e) => setFilter(e.target.value as Status | "")}>
          <option value="">All</option>
          {STATUSES.map((s) => <option key={s}>{s}</option>)}
        </select>
      </div>
      {error && <p role="alert">{error}</p>}
      <table>
        <thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Status history</th><th /></tr></thead>
        <tbody>
          {data?.applications.map((a) => (
            <tr key={a.id}>
              <td>{a.company}</td><td>{a.role}</td>
              <td>
                <select aria-label={`status-${a.id}`} value={a.status} onChange={(e) => act(Q.update, { id: a.id, s: e.target.value })}>
                  {STATUSES.map((s) => <option key={s}>{s}</option>)}
                </select>
              </td>
              <td>
                <details>
                  <summary>{a.events.length} status events</summary>
                  <ol>
                    {a.events.map((event, index) => (
                      <li key={`${event.at}-${index}`}>
                        {event.status}{" "}<time dateTime={event.at}>{new Date(event.at).toLocaleString()}</time>
                      </li>
                    ))}
                  </ol>
                </details>
              </td>
              <td><button onClick={() => act(Q.remove, { id: a.id })}>Delete</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
