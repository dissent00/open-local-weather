/// One provider made of several, tried in order — upstream ROADMAP item 81.
///
/// WHY IT EXISTS, measured on the server. Over 2026-08-28 to 09-21 the evening
/// run failed on Gemini 5xx five times in 29 runs and the morning run never did
/// in 25 (one-sided Fisher p = 0.038). Every one was a 500 or 503 after four
/// attempts across roughly eight and a half minutes, so the vendor was shedding
/// load for longer than any retry schedule this project would accept. Waiting
/// does not fix that; another vendor does.
///
/// HOW THIS DIFFERS FROM THE PYTHON SIDE, and it is not a port defect. There,
/// the spend cap is ATTACHED to a provider after construction, so the wrapper
/// has to forward those assignments down to its children. Here the hooks are
/// final constructor parameters, so each provider is built already holding its
/// own — this class only chooses between them, and has no plumbing to do.
library;

import 'provider.dart';
import 'schema.dart';

class FallbackProvider implements LlmProvider {
  final List<LlmProvider> providers;

  /// Diagnostics for the caller, in the order they happened. A deployment
  /// served by its second choice looks exactly like one served by its first,
  /// and which it was is the whole reliability question this answers.
  final void Function(String message)? onFallback;

  FallbackProvider(this.providers, {this.onFallback}) {
    if (providers.isEmpty) {
      throw ArgumentError('FallbackProvider needs at least one provider');
    }
  }

  /// The first entry's model. The record names what this deployment chose;
  /// which entry actually served a given call is reported through
  /// [onFallback] as it happens.
  @override
  String get model => providers.first.model;

  @override
  Future<T> generate<T>({
    required String systemPrompt,
    required String userPrompt,
    required ResponseShape<T> shape,
  }) async {
    Object? last;
    for (var i = 0; i < providers.length; i++) {
      final provider = providers[i];
      try {
        return await provider.generate(
          systemPrompt: systemPrompt,
          userPrompt: userPrompt,
          shape: shape,
        );
      } on LlmUnavailableError catch (e) {
        // ONLY this one. A response that failed schema validation is the
        // prompt, the schema, or this model's ability to follow them, and the
        // next model is handed exactly the same two — so falling back would
        // pay twice to be told the same thing, and hide the real fault behind
        // the second message.
        last = e;
        final left = providers.length - i - 1;
        onFallback?.call(
          '${provider.model} is unavailable: ${e.message}. '
          '${left > 0 ? "Falling back, $left provider(s) left." : "No providers left."}',
        );
      }
    }

    // The LAST failure, with its type intact: the caller branches on whether
    // this is an LlmUnavailableError, and wrapping it would change what
    // happens above.
    throw last!;
  }
}
