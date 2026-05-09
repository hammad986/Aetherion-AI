const jsdom = require("jsdom");
const { JSDOM } = jsdom;

const html = `
<!DOCTYPE html>
<html>
<head>
  <script>
    window.NX = window.NX || { boot: true };
    console.log("Script 1 executed");
  </script>
  <script>
    const NX = { ui: true };
    function myTestFn() { return true; }
    console.log("Script 2 executed");
  </script>
</head>
<body></body>
</html>
`;

const dom = new JSDOM(html, { runScripts: "dangerously" });
const window = dom.window;

// Check if there are any errors or if functions are accessible
console.log("window.myTestFn exists:", typeof window.myTestFn === 'function');
