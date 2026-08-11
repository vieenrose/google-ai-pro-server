# Import Google Gemini export conversations (Takeout / app export) into Discourse.
#
# For each JSON file (one conversation each):
#   - creates one topic per conversation in the "Gemini 匯入" category
#   - user messages -> the importing user; assistant messages -> the `gemini` bot
#   - backdates topic/posts to the original created_at timestamps (Discourse standard)
#   - model metadata -> tags (gemini, <folder-prefix>)
#   - Gemini thinking blocks -> <details class="ai-thinking"> (Discourse AI convention)
#   - attachments/images (opaque export refs) -> placeholder note
#
# Run inside the Discourse container:
#   su discourse -c "bundle exec rails runner /tmp/import_gemini_conversations.rb"
# Expects the JSON files at /shared/Gemini_import/ (i.e. host:
# /var/discourse/shared/standalone/Gemini_import/).
#
require "json"
require "time"

LUIGI = User.find_by(username: "luigi")
GEMINI = User.find_by(username: "gemini")
abort "missing users" if LUIGI.nil? || GEMINI.nil?

CAT = Category.find_by(name: "Gemini 匯入") || Category.create!(name: "Gemini 匯入", color: "6C47D6", text_color: "FFFFFF", user: LUIGI)
puts "category: #{CAT.id}"

def sanitize_tag(s)
  s.to_s.gsub(/[^\p{L}\p{N}_-]/, "_").gsub(/_+/, "_").gsub(/^_|_$/, "")[0..30]
end

def first_user_text(items)
  items.each do |it|
    next unless it["role"] == "user"
    t = it.dig("contents").to_a.map { |c| c["content"] if c["type"] == "text" }.compact.join(" ").strip
    return t if t.present?
  end
  nil
end

def title_from(text)
  t = text.to_s.gsub(/\s+/, " ").strip
  t = t[0..55]
  t += "…" if t.length == 56
  t.presence || "Gemini 對話"
end

def content_to_raw(contents)
  parts = []
  contents.to_a.each do |c|
    case c["type"]
    when "text"
      parts << c["content"].to_s
    when "thinking"
      parts << "<details class='ai-thinking'><summary>Thinking</summary>\n\n#{c['content'].to_s}\n\n</details>"
    when "attachment", "image"
      parts << "*[附件/圖片：匯出檔未包含內容]*"
    end
  end
  parts.join("\n\n").strip
end

files = Dir["/shared/Gemini_import/*.json"].sort
puts "files: #{files.size}"
ok = 0
failed = []
files.each do |f|
  begin
    items = JSON.parse(File.read(f))
    next if items.empty?
    items = items.sort_by { |it| it["created_at"].to_s }
    first_ts = items.first["created_at"].to_s
    first_text = first_user_text(items)
    title = title_from(first_text)
    tags = ["gemini", "gemini-import"]
    fbase = File.basename(f, ".json")
    if fbase =~ /^\(([^)]+)\)/
      tag = sanitize_tag($1)
      tags << tag if tag.present? && tag.length > 1
    end

    topic = PostCreator.create!(
      LUIGI,
      title: title,
      raw: "(從 Gemini 匯入的對話，共 #{items.size} 則訊息)",
      category: CAT.id,
      tags: tags,
      created_at: Time.zone.parse(first_ts),
      skip_validations: true,
    )
    tid = topic.topic_id

    items.each do |it|
      raw = content_to_raw(it["contents"])
      next if raw.blank?
      author = it["role"] == "assistant" ? GEMINI : LUIGI
      ts = it["created_at"].to_s
      PostCreator.create!(
        author,
        topic_id: tid,
        raw: raw,
        created_at: Time.zone.parse(ts),
        skip_validations: true,
      )
    end
    ok += 1
    puts "OK #{ok}: #{title[0..40]} (#{items.size} msgs)" if ok % 10 == 0 || ok <= 3
  rescue => e
    failed << [File.basename(f), e.message[0..120]]
    puts "FAIL #{File.basename(f)}: #{e.message[0..120]}"
  end
end
puts "DONE: ok=#{ok} failed=#{failed.size}"
failed.each { |f, e| puts "  #{f}: #{e}" }
