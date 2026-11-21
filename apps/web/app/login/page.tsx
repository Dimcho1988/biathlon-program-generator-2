import Image from "next/image";
import Link from "next/link";
import { redirect } from "next/navigation";
import { SpeedLoadCacheReset } from "../../components/speed-load-cache-reset";
import { LoginMethods } from "../../components/login-methods";
import { InstallApp } from "../../components/install-app";
import { supabaseAuthConfigured } from "../../lib/supabase/config";
import { createClient } from "../../lib/supabase/server";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; reauth?: string }>;
}) {
  const { error, reauth } = await searchParams;
  if (!supabaseAuthConfigured()) return <main className="account-page"><SpeedLoadCacheReset/><section className="account-card">
    <p className="section-kicker">onFlows account</p><h1>Входът още не е активиран</h1>
    <p>Supabase Auth ще бъде включен първо в staging след настройване на публичния ключ.</p>
    <Link href="/">Назад към приложението</Link>
  </section></main>;
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  if (data?.claims?.sub && reauth !== "1") redirect("/account");
  return <main className="account-page"><SpeedLoadCacheReset/><section className="account-card">
    <Link className="brand" href="/"><Image src="/brand/onflows-mark.png" alt="" width={33} height={40} /><span>onFlows</span></Link>
    <div><p className="section-kicker">Защитен вход</p><h1>Твоят onFlows акаунт</h1>
      <p>{reauth === "1" ? "Потвърди входа си, за да зададеш или смениш паролата." : "Влез с парола или защитен email линк. Входът се запазва на това устройство."}</p></div>
    <LoginMethods callbackError={error === "callback"} reauthenticate={reauth === "1"} />
    <small>Intervals ще се свързва след входа и ще служи само като източник на тренировъчни данни.</small>
    <InstallApp compact />
  </section></main>;
}
