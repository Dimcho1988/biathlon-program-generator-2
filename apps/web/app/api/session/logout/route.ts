import { NextResponse } from "next/server";
import { ATHLETE_SESSION_COOKIE } from "../../../../lib/athlete-session";

export async function POST() {
  const response = new NextResponse(null, { status: 303, headers: { Location: "/" } });
  response.cookies.set({
    name: ATHLETE_SESSION_COOKIE,
    value: "",
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: 0,
  });
  return response;
}
