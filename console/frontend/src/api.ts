// Minimal typed GraphQL client (fetch). The Vite dev server / nginx proxy /query to the Go backend.
export type Status = "SAVED" | "APPLIED" | "OA" | "INTERVIEW" | "OFFER" | "REJECTED";
export const STATUSES: Status[] = ["SAVED", "APPLIED", "OA", "INTERVIEW", "OFFER", "REJECTED"];
export interface StatusEvent { status: Status; at: string }
export interface Application { id: string; company: string; role: string; url?: string | null; status: Status; updatedAt: string; events: StatusEvent[] }

export async function gql<T>(query: string, variables: Record<string, unknown> = {}, token?: string | null): Promise<T> {
  const res = await fetch("/query", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  if (body.errors?.length) throw new Error(body.errors[0].message);
  return body.data as T;
}

export const Q = {
  login: `mutation($e:String!,$p:String!){login(email:$e,password:$p){token user{email}}}`,
  register: `mutation($e:String!,$p:String!){register(email:$e,password:$p){token user{email}}}`,
  list: `query($s:Status,$q:String){applications(status:$s,search:$q){id company role url status updatedAt events{status at}} stats{total byStatus{status count}}}`,
  create: `mutation($in:NewApplication!){createApplication(input:$in){id}}`,
  update: `mutation($id:ID!,$s:Status!){updateStatus(id:$id,status:$s){id status}}`,
  remove: `mutation($id:ID!){deleteApplication(id:$id)}`,
};
