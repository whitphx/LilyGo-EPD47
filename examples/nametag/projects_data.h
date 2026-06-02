#pragma once
#include <stddef.h>

struct ProjectRow {
    const char* name;
    const char* desc;
    const char* stars;
};

const ProjectRow projects[] = {
    { "Stlite", "In-browser Streamlit", "1.6k" },
    { "Streamlit-WebRTC", "Real-time A/V on Streamlit", "1.7k" },
};
const size_t projects_count = 2;
