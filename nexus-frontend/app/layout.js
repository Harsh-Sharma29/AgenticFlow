import './globals.css';

export const metadata = {
  title: 'AgenticFlow Orchestrator',
  description: 'Enterprise-grade multi-agent AI orchestrator',
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <div className="app-layout">
          {children}
        </div>
      </body>
    </html>
  );
}
