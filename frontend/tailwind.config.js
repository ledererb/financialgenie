/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        field: {
          mapped: "#22c55e",
          lowconf: "#eab308",
          unmapped: "#f97316",
          selected: "#3b82f6",
          group: "#a855f7",
          static: "#9ca3af",
        },
      },
    },
  },
  plugins: [],
};
