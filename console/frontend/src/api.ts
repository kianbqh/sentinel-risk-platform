// The proxy keeps API requests on the same origin. Tokens stay in memory.
export type Status = "OPEN" | "INVESTIGATING" | "RESOLVED";
export type Label = "FRAUD" | "BENIGN" | "UNCERTAIN";
export interface Analyst { id: string; email: string }
export interface CaseEvent { action: string; note: string; actorId: string | null; at: string }
export interface CaseLabel { value: Label; note: string; actorId: string; at: string }
export interface RiskCase { id: string; status: Status; assigneeId: string | null; updatedAt: string; events: CaseEvent[]; labels: CaseLabel[] }
export interface Alert { id: string; eventTime: string; source: string; entityType: string; entityId: string; riskScore: number | null; threshold: number | null; severity: string; summary: string; modelName: string; modelVersion: string; evidence: string; traceId: string; case: RiskCase }
export interface Stats { total: number; open: number; investigating: number; resolved: number; assigned: number }
export interface QueueData { me: Analyst; analysts: Analyst[]; alerts: Alert[]; stats: Stats }

export async function gql<T>(query: string, variables: Record<string, unknown> = {}, token?: string | null): Promise<T> {
  const res = await fetch("/graphql", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  if (!res.ok) throw new Error(`Request failed (${res.status})`);
  if (body.errors?.length) throw new Error(body.errors[0].message);
  if (!body.data || typeof body.data !== "object") throw new Error("Invalid GraphQL response");
  return body.data as T;
}

export const Q = {
  login: `mutation($e:String!,$p:String!){login(email:$e,password:$p){token user{email}}}`,
  register: `mutation($e:String!,$p:String!){register(email:$e,password:$p){token user{email}}}`,
  list: `query($source:String,$status:CaseStatus,$severity:String,$search:String,$assigneeId:ID){me{id email} analysts{id email} alerts(source:$source,status:$status,severity:$severity,search:$search,assigneeId:$assigneeId,limit:50){id eventTime source entityType entityId riskScore threshold severity summary modelName modelVersion evidence traceId case{id status assigneeId updatedAt events{action note actorId at} labels{value note actorId at}}} stats{total open investigating resolved assigned}}`,
  assign: `mutation($id:ID!,$analyst:ID!){assignCase(id:$id,assigneeId:$analyst){id status assigneeId}}`,
  resolve: `mutation($id:ID!,$note:String!){resolveCase(id:$id,note:$note){id status}}`,
  label: `mutation($id:ID!,$value:LabelValue!,$note:String!){labelCase(id:$id,value:$value,note:$note){id labels{value actorId note}}}`,
};
