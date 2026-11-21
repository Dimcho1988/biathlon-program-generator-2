// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginMethods } from "../components/login-methods";
import { PasswordSettings } from "../components/password-settings";

// Synthetic credentials are confined to mocked requests; no account is contacted.
const email = "athlete@example.test";
const password = "Synthetic-new-password-42";
const currentPassword = "Synthetic-current-password-7";
let root: Root, container: HTMLDivElement;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

const render = (element: ReactNode) => act(async () => root.render(element));
const input = (name: string) => container.querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
const submitButton = () => container.querySelector<HTMLButtonElement>('button[type="submit"]')!;
const submit = () => act(async () => {
  container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
});
const clickButton = (text: string) => act(async () => {
  [...container.querySelectorAll("button")].find(button => button.textContent === text)!.click();
});
function fillPasswordSettings(newPassword = password, confirmation = newPassword) {
  input("current_password").value = currentPassword;
  input("password").value = newPassword;
  input("confirmation").value = confirmation;
}
function pendingResponse() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>(done => { resolve = done; });
  return { promise, resolve };
}

describe("login method interactions", () => {
  it("switches between password and email links and sends only the selected method's inputs", async () => {
    fetchMock.mockResolvedValue(Response.json({ sent: true }));
    await render(<LoginMethods />);
    expect(input("password").autocomplete).toBe("current-password");
    expect(container.querySelector('[aria-pressed="true"]')!.textContent).toBe("С парола");

    await clickButton("С email линк");
    expect(container.querySelector('input[name="password"]')).toBeNull();
    expect(input("email").autocomplete).toBe("email");
    input("email").value = email;
    await submit();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/magic-link");
    expect(init).toMatchObject({ method: "POST", headers: { "Content-Type": "application/json" } });
    expect(JSON.parse(String(init!.body))).toEqual({ email });
    expect(container.querySelector('[role="status"]')!.textContent).toContain("Провери email-а си");
    expect(submitButton().disabled).toBe(true);

    await clickButton("С парола");
    expect(input("password").value).toBe("");
    expect(submitButton().textContent).toBe("Влез с парола");
    expect(container.querySelector('[aria-pressed="true"]')!.textContent).toBe("С парола");
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("offers email links for a first login or a forgotten password without making a request", async () => {
    await render(<LoginMethods />);
    await clickButton("Първи вход или забравена парола? Използвай email линк.");
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(submitButton().textContent).toBe("Изпрати защитен линк");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([400, 401])("shows the same generic invalid-credentials message for HTTP %i", async status => {
    fetchMock.mockResolvedValue(Response.json({ reason: "private-provider-detail" }, { status }));
    await render(<LoginMethods />);
    input("email").value = email;
    input("password").value = password;
    await submit();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/auth/password");
    expect(init).toMatchObject({ method: "POST", headers: { "Content-Type": "application/json" } });
    expect(JSON.parse(String(init!.body))).toEqual({ email, password });
    expect(container.querySelector('[role="alert"]')!.textContent).toBe("Email-ът или паролата са невалидни. Ако още нямаш парола, влез с email линк.");
    expect(container.textContent).not.toContain("private-provider-detail");
    expect(submitButton().disabled).toBe(false);
  });

  it("distinguishes rate-limited password login from invalid credentials", async () => {
    fetchMock.mockResolvedValue(Response.json({}, { status: 429 }));
    await render(<LoginMethods />);
    input("email").value = email;
    input("password").value = password;
    await submit();
    expect(container.querySelector('[role="alert"]')!.textContent).toBe("Твърде много опити. Изчакай малко и опитай отново.");
    expect(container.textContent).not.toContain("Email-ът или паролата са невалидни");
    expect(submitButton().disabled).toBe(false);
  });

  it("allows retry after a failed connection without exposing internal errors", async () => {
    fetchMock.mockRejectedValue(new Error("private-network-detail"));
    await render(<LoginMethods />);
    input("email").value = email;
    input("password").value = password;
    await submit();
    expect(container.querySelector('[role="alert"]')!.textContent).toBe("Входът временно не е достъпен. Опитай отново.");
    expect(container.textContent).not.toContain("private-network-detail");
    expect(submitButton().disabled).toBe(false);
  });

  it("prevents duplicate password requests while login is pending", async () => {
    const pending = pendingResponse();
    fetchMock.mockReturnValue(pending.promise);
    await render(<LoginMethods />);
    input("email").value = email;
    input("password").value = password;
    await submit();
    expect(submitButton().disabled).toBe(true);
    expect(submitButton().textContent).toBe("Влизане…");
    await submit();
    expect(fetchMock).toHaveBeenCalledOnce();
    // Failed login deliberately avoids window.location.assign in JSDOM.
    await act(async () => pending.resolve(Response.json({}, { status: 401 })));
    expect(submitButton().disabled).toBe(false);
  });

  it("starts with a new email link after an expired auth callback", async () => {
    await render(<LoginMethods callbackError />);
    expect(container.querySelector('input[type="password"]')).toBeNull();
    expect(container.querySelector('[role="alert"]')!.textContent).toBe("Линкът е невалиден или е изтекъл. Изпрати си нов линк.");
    expect(submitButton().textContent).toBe("Изпрати защитен линк");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("starts reauthentication with email while keeping password login available", async () => {
    await render(<LoginMethods reauthenticate />);
    expect(container.querySelector('[aria-pressed="true"]')!.textContent).toBe("С email линк");
    expect(container.querySelector('input[type="password"]')).toBeNull();
    await clickButton("С парола");
    expect(input("password")).not.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("password settings interactions", () => {
  it("rejects mismatched passwords locally and preserves the entered values", async () => {
    await render(<PasswordSettings />);
    fillPasswordSettings(password, "Synthetic-different-password-81");
    await submit();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')!.textContent).toBe("Двете нови пароли не съвпадат.");
    expect(input("password").value).toBe(password);
    expect(input("current_password").value).toBe(currentPassword);
    expect(submitButton().disabled).toBe(false);
  });

  it.each([
    ["too few characters", "Synthetic11"],
    ["too many ASCII bytes", "A".repeat(73)],
    ["too many UTF-8 bytes", "Ж".repeat(37)],
  ])("rejects %s locally without a password request", async (_description, invalidPassword) => {
    await render(<PasswordSettings />);
    fillPasswordSettings(invalidPassword);
    await submit();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')!.textContent).toContain("Използвай поне 12 символа");
    expect(input("password").value).toBe(invalidPassword);
    expect(submitButton().disabled).toBe(false);
  });

  it("saves once while pending, sends the current password and resets all password fields on success", async () => {
    const pending = pendingResponse();
    fetchMock.mockReturnValue(pending.promise);
    await render(<PasswordSettings />);
    fillPasswordSettings();
    await submit();
    expect(submitButton().disabled).toBe(true);
    expect(submitButton().textContent).toBe("Запазване…");
    await submit();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/account/password");
    expect(init).toMatchObject({ method: "POST", headers: { "Content-Type": "application/json" } });
    expect(JSON.parse(String(init!.body))).toEqual({ password, currentPassword });

    await act(async () => pending.resolve(Response.json({ saved: true })));
    expect(container.querySelector('[role="status"]')!.textContent).toContain("Паролата е запазена");
    expect(container.querySelector('[role="alert"]')).toBeNull();
    for (const name of ["current_password", "password", "confirmation"]) expect(input(name).value).toBe("");
    expect(submitButton().disabled).toBe(false);
  });

  it("offers email reauthentication and keeps inputs when the recent login has expired", async () => {
    fetchMock.mockResolvedValue(Response.json({ reason: "reauthenticate" }, { status: 401 }));
    await render(<PasswordSettings />);
    fillPasswordSettings();
    await submit();
    const alert = container.querySelector('[role="alert"]')!;
    expect(alert.textContent).toContain("Потвърди входа си отново, преди да смениш паролата");
    expect(alert.querySelector("a")!.getAttribute("href")).toBe("/login?reauth=1");
    expect(alert.querySelector("a")!.textContent).toBe("Влез с email линк");
    expect(input("password").value).toBe(password);
    expect(input("confirmation").value).toBe(password);
    expect(input("current_password").value).toBe(currentPassword);
    expect(submitButton().disabled).toBe(false);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("uses a generic save failure for unknown server details and keeps the values for retry", async () => {
    fetchMock.mockResolvedValue(Response.json({ reason: "private-provider-detail" }, { status: 502 }));
    await render(<PasswordSettings />);
    fillPasswordSettings();
    await submit();
    expect(container.querySelector('[role="alert"]')!.textContent).toBe("Паролата не беше запазена. Провери връзката и опитай отново.");
    expect(container.textContent).not.toContain("private-provider-detail");
    expect(input("password").value).toBe(password);
    expect(submitButton().disabled).toBe(false);
  });
});
