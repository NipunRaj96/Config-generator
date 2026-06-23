with open("scratch/jeevan_unescaped.html", "r", encoding="utf-8") as f:
    content = f.read()

idx = content.find('[{"Remote_Job":')
if idx != -1:
    print("Found JSON array at index:", idx)
    # Print 300 characters before the array to see the container tag
    print("--- 300 characters before ---")
    print(content[max(0, idx - 300):idx])
    # Print first 500 characters of the array
    print("--- 500 characters of JSON array ---")
    print(content[idx:idx+500])
else:
    print("JSON array not found")
