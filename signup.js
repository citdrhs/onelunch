document.querySelectorAll(".eye").forEach(button => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.target);
  
      if (input.type === "password") {
        input.type = "text";
        button.textContent = "!";
      } else {
        input.type = "password";
        button.textContent = "👁";
      }
    });
  });
  
