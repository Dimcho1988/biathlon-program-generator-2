const required = (name: "NEXT_PUBLIC_SUPABASE_URL" | "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY") => {
  const value = process.env[name]?.trim();
  if (!value) throw new Error("Supabase Auth configuration is incomplete");
  return value;
};

export const supabaseAuthConfigured = () => Boolean(
  process.env.NEXT_PUBLIC_SUPABASE_URL?.trim()
  && process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim(),
);

export const supabasePublicConfig = () => ({
  url: required("NEXT_PUBLIC_SUPABASE_URL"),
  publishableKey: required("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY"),
});
