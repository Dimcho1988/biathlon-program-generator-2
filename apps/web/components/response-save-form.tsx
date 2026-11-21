"use client";
import {useState, type FormEvent} from "react";
import {useRouter} from "next/navigation";
import Link from "next/link";

export function SaveForm({kind,children,build,disabled=false}:{kind:string;children:React.ReactNode;build:(f:FormData)=>unknown;disabled?:boolean}) {
  const router = useRouter();
  const [busy,setBusy] = useState(false), [message,setMessage] = useState("");
  async function submit(e:FormEvent<HTMLFormElement>) {
    e.preventDefault(); if(busy || disabled)return;
    const form = new FormData(e.currentTarget); setBusy(true);setMessage("");
    try {
      const r=await fetch("/api/athlete/response",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind,payload:build(form)})});
      const result=await r.json();
      if(!r.ok) throw new Error(result.error||"Записването не успя.");
      setMessage("Запазено успешно."); router.refresh();
    } catch(error){setMessage(error instanceof Error?error.message:"Опитайте отново.");}
    finally{setBusy(false);}
  }
  return <form onSubmit={submit} className="response-form"><fieldset disabled={disabled||busy}>{children}<button className="action-button" disabled={busy||disabled} type="submit">{busy?"Записваме…":"Запази"}</button></fieldset><p role="status" aria-live="polite">{message}</p>{kind === "daily" && message === "Запазено успешно." && <Link href="/management">Към програмата · обнови според записаната оценка →</Link>}</form>;
}

