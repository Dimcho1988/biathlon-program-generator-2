// @vitest-environment jsdom
import {renderToStaticMarkup} from "react-dom/server";
import {expect, it, vi} from "vitest";
import {ManagementPreparationNotice} from "../components/management-preparation-notice";

vi.mock("next/navigation", () => ({useRouter: () => ({refresh: vi.fn()})}));
const preparation = {status: "READY" as const, prepared_at: "2026-11-13T07:05:00Z", evaluated_on: "2026-11-13", reason: null};

it("shows the saved date and time in the athlete timezone without starting refresh", () => {
  const html = renderToStaticMarkup(<ManagementPreparationNotice preparation={preparation} timezone="Europe/Sofia"/>);
  expect(html).toContain('aria-label="Актуалност на изгледа"');
  expect(html).toContain('dateTime="2026-11-13T07:05:00Z"');
  expect(html).toContain("09:05");
  expect(html).toContain("13.11.2026");
  expect(html).not.toContain("Провери отново");
  expect(html).not.toContain("Показана е последната налична програма");
});

it.each([
  ["INPUTS_CHANGED", "Има нови данни или променени настройки"],
  ["DAY_CHANGED", "Подготвя се оценката за новия ден"],
  ["MODEL_CHANGED", "Изчисленията се обновяват след промяна на модела"],
])("explains %s while retaining the older timestamp", (reason, message) => {
  const html = renderToStaticMarkup(<ManagementPreparationNotice
    preparation={{...preparation, status: "UPDATING", reason}} timezone="Europe/Sofia"/>);
  expect(html).toContain(message);
  expect(html).toContain("Дозите може да не отразяват последните промени");
  expect(html).toContain('dateTime="2026-11-13T07:05:00Z"');
  expect(html).toContain("Провери отново");
});
