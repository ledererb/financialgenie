import { useState } from "react";
import MappingStudio from "@/components/MappingStudio";
import AdminPage from "@/components/AdminPage";

export default function App() {
  const [view, setView] = useState<"studio" | "admin">("studio");

  if (view === "admin") {
    return <AdminPage onBack={() => setView("studio")} />;
  }

  return <MappingStudio onOpenAdmin={() => setView("admin")} />;
}
