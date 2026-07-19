export function initRouteError(error: unknown, fallback: string): Response {
  const detail = error instanceof Error ? error.message : String(error);
  if (isEmbeddingUnavailable(detail)) {
    return Response.json(
      {
        error:
          "The embedding service is not reachable. Start it with `~/embeddinggemma.c/build/embeddinggemma-metal`, or run `hexis up`, then retry this step.",
        detail,
      },
      { status: 503 }
    );
  }
  return Response.json(
    {
      error: fallback,
      detail,
    },
    { status: 500 }
  );
}

export function isEmbeddingUnavailable(message: string): boolean {
  return /Embedding service not available|Failed to get embeddings|host\.docker\.internal.*11434|port 11434|ECONNREFUSED/i.test(
    message
  );
}
