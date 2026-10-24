/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#14161A',
        paper: '#FAFAF8',
        line: '#E4E2DC',
        slate: '#6B6F76',
        signal: '#FF5A36',
        signalDark: '#E0431F',
        status: {
          gray: '#9CA3AF',
          blue: '#4C7EF3',
          amber: '#E0A428',
          violet: '#8B5CF6',
          indigo: '#5B5FEF',
          green: '#2FA96B',
          orange: '#E0782E',
          red: '#E24C4C',
        },
      },
      fontFamily: {
        display: ['"Space Grotesk"', 'sans-serif'],
        sans: ['"IBM Plex Sans"', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'monospace'],
      },
      borderRadius: {
        sm: '3px',
        DEFAULT: '4px',
        md: '6px',
      },
    },
  },
  plugins: [],
};
