import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, vi } from "vitest";
import App from "./App";
import { Board } from "./Board";

function mockFetch(handler: (body: { query: string; variables: Record<string, unknown> }) => unknown) {
  const f = vi.fn(async (_url: string, init: RequestInit) => ({ json: async () => ({ data: handler(JSON.parse(init.body as string)) }) }));
  vi.stubGlobal("fetch", f);
  return f;
}

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

test("login stores the JWT and shows the board", async () => {
  mockFetch(({ query }) =>
    query.includes("login") ? { login: { token: "jwt-123", user: { email: "a@b.io" } } }
      : { applications: [], stats: { total: 0, byStatus: [] } });
  render(<App />);
  await userEvent.type(screen.getByLabelText("email"), "a@b.io");
  await userEvent.type(screen.getByLabelText("password"), "password123");
  await userEvent.click(screen.getByRole("button", { name: "Log in" }));
  await waitFor(() => expect(screen.getByTestId("stats")).toHaveTextContent("Total: 0"));
  expect(localStorage.getItem("token")).toBe("jwt-123");
});

test("shows GraphQL errors from the server", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ json: async () => ({ errors: [{ message: "invalid email or password" }] }) })));
  render(<App />);
  await userEvent.type(screen.getByLabelText("email"), "a@b.io");
  await userEvent.type(screen.getByLabelText("password"), "wrongpass1");
  await userEvent.click(screen.getByRole("button", { name: "Log in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("invalid email or password");
});

test("board lists applications, sends bearer token and creates new ones", async () => {
  const f = mockFetch(({ query }) =>
    query.includes("createApplication") ? { createApplication: { id: "2" } }
      : { applications: [{ id: "1", company: "Stripe", role: "MLE Intern", status: "OA", updatedAt: "", events: [{ status: "APPLIED", at: "2026-10-30T10:00:00Z" }, { status: "OA", at: "2026-10-31T10:00:00Z" }] }],
          stats: { total: 1, byStatus: [{ status: "OA", count: 1 }] } });
  render(<Board token="tok" />);
  expect(await screen.findByText("Stripe")).toBeInTheDocument();
  expect(screen.getByTestId("stats")).toHaveTextContent("OA 1");
  await userEvent.click(screen.getByText("2 status events"));
  expect(screen.getByText("APPLIED", { selector: "li" })).toBeVisible();
  await userEvent.type(screen.getByLabelText("company"), "NVIDIA");
  await userEvent.type(screen.getByLabelText("role"), "DL Intern");
  await userEvent.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(f.mock.calls.some(([, i]) => (i.body as string).includes("NVIDIA"))).toBe(true));
  expect((f.mock.calls[0][1].headers as Record<string, string>).Authorization).toBe("Bearer tok");
});

test("board reports failed status updates and deletes, then clears the error after a successful reload", async () => {
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    const { query } = JSON.parse(init.body as string);
    if (query.includes("updateStatus") || query.includes("deleteApplication")) {
      return { json: async () => ({ errors: [{ message: "application could not be changed" }] }) };
    }
    return { json: async () => ({ data: {
      applications: [{ id: "1", company: "Stripe", role: "Engineer", status: "OA", updatedAt: "", events: [] }],
      stats: { total: 1, byStatus: [{ status: "OA", count: 1 }] },
    } }) };
  }));
  render(<Board token="tok" />);
  await screen.findByText("Stripe");
  await userEvent.selectOptions(screen.getByLabelText("status-1"), "INTERVIEW");
  expect(await screen.findByRole("alert")).toHaveTextContent("application could not be changed");
  await userEvent.selectOptions(screen.getByLabelText("filter"), "OA");
  await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("application could not be changed");
});
