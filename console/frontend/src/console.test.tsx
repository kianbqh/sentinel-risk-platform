import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import App from "./App";
import { Board } from "./Queue";
import { gql, type QueueData } from "./api";

function queue(score: number | null = .98): QueueData {
  return { me: { id: '1', email: 'analyst@example.com' }, analysts: [{ id:'1', email:'analyst@example.com' }],
    stats:{ total:1, open:1, investigating:0, resolved:0, assigned:0 },
    alerts:[{ id:'test-alert', eventTime:'2026-11-01T01:00:00Z', source:'ato', entityType:'ip', entityId:'192.0.2.1', riskScore:score,
      threshold: score === null ? null : .5, severity:'high', summary:'Synthetic login-abuse window flagged', modelName:'test', modelVersion:'v1',
      evidence:JSON.stringify({synthetic:true,score_source:score === null ? 'none' : 'tfidf_lr'}), traceId:'trace',
      case:{id:'7',status:'OPEN',assigneeId:null,updatedAt:'',events:[{action:'INGESTED',note:'ato',actorId:null,at:'2026-11-01T01:00:00Z'}],labels:[]} }] };
}
function mock(handler: (query:string, variables:Record<string,unknown>) => unknown) {
  const fn = vi.fn(async (_url:string, init:RequestInit) => {const body=JSON.parse(init.body as string);return {ok:true,status:200,json:async()=>({data:handler(body.query,body.variables)})};});
  vi.stubGlobal('fetch',fn);return fn;
}
afterEach(()=>{vi.unstubAllGlobals();localStorage.clear();});
test('login opens analyst queue and does not persist JWT',async()=>{
  mock(query=>query.includes('login')?{login:{token:'token',user:{email:'analyst@example.com'}}}:queue());
  render(<App/>);await userEvent.type(screen.getByLabelText('email'),'analyst@example.com');await userEvent.type(screen.getByLabelText('password'),'password123');await userEvent.click(screen.getByRole('button',{name:'Log in'}));
  expect(await screen.findByRole('region',{name:'Alert queue'})).toBeVisible();expect(localStorage.length).toBe(0);
  await userEvent.click(screen.getByRole('button',{name:'Log out'}));expect(screen.getByRole('form',{name:'auth'})).toBeVisible();
});
test('analyst can assign, label and resolve a case',async()=>{
  const calls=mock(query=>query.startsWith('mutation')?{ok:true}:queue());render(<Board token="jwt"/>);
  await screen.findByRole('button',{name:'Assign case'});await userEvent.click(screen.getByRole('button',{name:'Assign case'}));await screen.findByText('Case assigned');
  await userEvent.selectOptions(screen.getByLabelText('Label'),'FRAUD');await userEvent.type(screen.getByLabelText('Decision note'),'reviewed');await userEvent.click(screen.getByRole('button',{name:'Save label'}));await screen.findByText('Label saved');
  await userEvent.click(screen.getByRole('button',{name:'Resolve case'}));await screen.findByText('Case resolved');
  const bodies=calls.mock.calls.map(([,init])=>JSON.parse(init.body as string));
  expect(bodies.some(b=>b.query.includes('assignCase')&&b.variables.id==='7'&&b.variables.analyst==='1')).toBe(true);
  expect(bodies.some(b=>b.query.includes('labelCase')&&b.variables.value==='FRAUD'&&b.variables.note==='reviewed')).toBe(true);
  expect(bodies.some(b=>b.query.includes('resolveCase'))).toBe(true);
  expect((calls.mock.calls[0][1].headers as Record<string,string>).Authorization).toBe('Bearer jwt');
});
test('filters become GraphQL variables',async()=>{
  const calls=mock(()=>queue());render(<Board token="jwt"/>);await screen.findByRole('button',{name:'Assign case'});
  await userEvent.selectOptions(screen.getByLabelText('Source'),'ato');await userEvent.selectOptions(screen.getByLabelText('Case status'),'OPEN');await userEvent.click(screen.getByLabelText('Assigned to me'));
  await waitFor(()=>expect(calls.mock.calls.some(([,init])=>{const b=JSON.parse(init.body as string);return b.variables.source==='ato'&&b.variables.status==='OPEN'&&b.variables.assigneeId==='1';})).toBe(true));
});
test('no probability is displayed explicitly',async()=>{
  mock(()=>queue(null));render(<Board token="jwt"/>);expect(await screen.findByText('Unavailable — label decision only')).toBeVisible();expect(screen.getByText('Label-based route')).toBeVisible();
});
test('GraphQL errors returned with HTTP200 are failures',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,status:200,json:async()=>({errors:[{message:'delivery failed'}]})})));
  await expect(gql('{stats{total}}')).rejects.toThrow('delivery failed');
});
test('failed mutation stays visible to the analyst',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(_url:string,init:RequestInit)=>({ok:true,status:200,json:async()=>JSON.parse(init.body as string).query.includes('assignCase')?{errors:[{message:'analyst unavailable'}]}:{data:queue()}})));
  render(<Board token="jwt"/>);await screen.findByRole('button',{name:'Assign case'});await userEvent.click(screen.getByRole('button',{name:'Assign case'}));expect(await screen.findByRole('alert')).toHaveTextContent('analyst unavailable');
});
