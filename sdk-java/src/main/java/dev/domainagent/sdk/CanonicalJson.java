package dev.domainagent.sdk;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/** Canonical JSON profile defined by protocol/enterprise/signing-v1.md. */
public final class CanonicalJson {
  private static final ObjectMapper JSON = new ObjectMapper();
  private static final Comparator<String> CODE_POINT_ORDER = CanonicalJson::compareCodePoints;

  private CanonicalJson() {}

  public static byte[] utf8(Object value) {
    StringBuilder out = new StringBuilder();
    append(out, value);
    return out.toString().getBytes(StandardCharsets.UTF_8);
  }

  public static String sha256(Object value) {
    try {
      byte[] digest = MessageDigest.getInstance("SHA-256").digest(utf8(value));
      StringBuilder hex = new StringBuilder(digest.length * 2);
      for (byte b : digest) hex.append(String.format("%02x", b));
      return hex.toString();
    } catch (NoSuchAlgorithmException error) {
      throw new IllegalStateException("SHA-256 is unavailable", error);
    }
  }

  private static void append(StringBuilder out, Object value) {
    if (value == null) { out.append("null"); return; }
    if (value instanceof String text) { out.append(quote(text)); return; }
    if (value instanceof Boolean bool) { out.append(bool ? "true" : "false"); return; }
    if (value instanceof Byte || value instanceof Short || value instanceof Integer || value instanceof Long || value instanceof BigInteger) {
      out.append(value); return;
    }
    if (value instanceof Float || value instanceof Double || value instanceof java.math.BigDecimal) {
      throw new ProtocolException("Floating-point values are not portable in Enterprise signing v1");
    }
    if (value instanceof List<?> list) {
      out.append('[');
      for (int index = 0; index < list.size(); index++) {
        if (index > 0) out.append(',');
        append(out, list.get(index));
      }
      out.append(']');
      return;
    }
    if (value instanceof Map<?, ?> raw) {
      TreeMap<String, Object> sorted = new TreeMap<>(CODE_POINT_ORDER);
      raw.forEach((key, item) -> {
        if (!(key instanceof String text)) throw new ProtocolException("Canonical JSON object keys must be strings");
        sorted.put(text, item);
      });
      out.append('{');
      boolean first = true;
      for (Map.Entry<String, Object> entry : sorted.entrySet()) {
        if (!first) out.append(',');
        first = false;
        out.append(quote(entry.getKey())).append(':');
        append(out, entry.getValue());
      }
      out.append('}');
      return;
    }
    throw new ProtocolException("Unsupported canonical JSON value: " + value.getClass().getName());
  }

  private static String quote(String value) {
    try {
      return JSON.writeValueAsString(value);
    } catch (JsonProcessingException error) {
      throw new ProtocolException("Unable to encode JSON string", error);
    }
  }

  private static int compareCodePoints(String left, String right) {
    int leftIndex = 0, rightIndex = 0;
    while (leftIndex < left.length() && rightIndex < right.length()) {
      int a = left.codePointAt(leftIndex), b = right.codePointAt(rightIndex);
      if (a != b) return Integer.compare(a, b);
      leftIndex += Character.charCount(a);
      rightIndex += Character.charCount(b);
    }
    return Integer.compare(left.length(), right.length());
  }
}
