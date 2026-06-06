/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#38bdf8',
          dark: '#0ea5e9',
        },
      },
    },
  },
  plugins: [],
}
