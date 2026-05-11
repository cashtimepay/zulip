import $ from "jquery";

import * as browser_history from "./browser_history.ts";
import * as channel from "./channel.ts";
import * as hash_util from "./hash_util.ts";
import {$t_html} from "./i18n.ts";
import type {Message} from "./message_store.ts";
import * as stream_topic_history from "./stream_topic_history.ts";
import * as stream_topic_history_util from "./stream_topic_history_util.ts";
import * as ui_report from "./ui_report.ts";
import * as util from "./util.ts";

const TOPIC_WORD_COUNT = 10;
const TOPIC_MAX_LENGTH = 60;

export function clean_for_topic_name(raw: string): string {
    let s = raw;

    s = s.replace(/```[\s\S]*?```/g, " ");
    s = s.replace(/`[^`]*`/g, " ");
    s = s.replace(/^[ \t]*>+[ \t]?/gm, " ");
    s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1");
    s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
    s = s.replace(/https?:\/\/\S+/g, " ");
    s = s.replace(/@_?\*\*[^*|]+(\|\d+)?\*\*/g, " ");
    s = s.replace(/@\*[^*]+\*/g, " ");
    s = s.replace(/:[a-z0-9_+\-]+:/gi, " ");
    s = s.replace(/\*\*([^*]+)\*\*/g, "$1");
    s = s.replace(/__([^_]+)__/g, "$1");
    s = s.replace(/(^|[^\w*])\*([^*\n]+)\*(?!\w)/g, "$1$2");
    s = s.replace(/(^|[^\w_])_([^_\n]+)_(?!\w)/g, "$1$2");
    s = s.replace(/~~([^~]+)~~/g, "$1");
    s = s.replace(/^[ \t]*#+\s*/gm, " ");
    s = s.replace(/^[ \t]*[*\-+]\s+/gm, " ");
    s = s.replace(/^[ \t]*\d+\.\s+/gm, " ");
    s = s.replace(
        /[\u{1F000}-\u{1FFFF}\u{2600}-\u{27BF}\u{1F300}-\u{1F9FF}]/gu,
        " ",
    );
    s = s.replace(/\s+/g, " ").trim();

    if (s === "") {
        return "untitled";
    }

    const words = s.split(" ").slice(0, TOPIC_WORD_COUNT);
    let topic = words.join(" ");

    if (topic.length > TOPIC_MAX_LENGTH) {
        topic = topic.slice(0, TOPIC_MAX_LENGTH);
        const last_space = topic.lastIndexOf(" ");
        if (last_space > 0) {
            topic = topic.slice(0, last_space);
        }
    }

    return topic;
}

function strip_html(html: string): string {
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    return tmp.textContent ?? "";
}

function get_message_text(message: Message): string {
    if (message.raw_content !== undefined && message.raw_content !== "") {
        return message.raw_content;
    }
    if (typeof message.content === "string") {
        return strip_html(message.content);
    }
    return "";
}

function resolve_topic_collision(stream_id: number, candidate: string): string {
    const existing = stream_topic_history.get_recent_topic_names(stream_id);
    const match = existing.find((t) => util.lower_same(t, candidate));
    return match ?? candidate;
}

export function execute_quote_to_new_topic(message: Message): void {
    if (message.type !== "stream") {
        return;
    }

    const stream_id = message.stream_id;
    const candidate = clean_for_topic_name(get_message_text(message));

    stream_topic_history_util.get_server_history(stream_id, () => {
        const new_topic = resolve_topic_collision(stream_id, candidate);

        if (util.lower_same(new_topic, message.topic)) {
            return;
        }

        channel.patch({
            url: "/json/messages/" + message.id,
            data: {
                propagate_mode: "change_one",
                topic: new_topic,
                send_notification_to_old_thread: false,
                send_notification_to_new_thread: false,
            },
            success(): void {
                const url = hash_util.by_stream_topic_url(stream_id, new_topic);
                browser_history.go_to_location(url);
            },
            error(xhr): void {
                const msg = channel.xhr_error_message(
                    $t_html({defaultMessage: "Failed to move message to a new topic."}),
                    xhr,
                );
                ui_report.error(msg, $("#home-error"));
            },
        });
    });
}
