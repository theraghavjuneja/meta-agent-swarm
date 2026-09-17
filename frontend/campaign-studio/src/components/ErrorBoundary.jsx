import { Component } from 'react';

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    console.error('Route error boundary caught:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex flex-col items-center justify-center text-center py-24 px-6">
          <h2 className="font-display text-lg font-semibold text-ink">Something broke</h2>
          <p className="mt-1.5 text-sm text-slate max-w-sm">
            This page ran into an unexpected error. Try refreshing — if it keeps happening, the
            backend may be unreachable.
          </p>
          <button
            type="button"
            onClick={() => this.setState({ hasError: false })}
            className="mt-4 px-4 py-2 rounded-sm bg-ink text-white text-sm font-medium hover:bg-ink/90"
          >
            Try again
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
